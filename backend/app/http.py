"""Native Django request validation, browser identity and byte-range streaming."""
import hmac
import inspect
import json
import os
import re
from django.http import FileResponse, HttpResponse, JsonResponse, StreamingHttpResponse
from django.urls import path
from pydantic import BaseModel, ValidationError
from .errors import APIError


class ApiHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.origins = set(os.getenv('MUSICBOX_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000,http://testserver').split(','))

    def __call__(self, request):
        response = None
        if request.method in {'POST', 'PATCH', 'PUT', 'DELETE'}:
            origin = request.headers.get('Origin')
            length = request.headers.get('Content-Length', '0')
            if origin and origin not in self.origins:
                response = JsonResponse({'detail': 'Origin not allowed'}, status=403)
            elif length.isdigit() and int(length) > 101 * 1024 * 1024:
                response = JsonResponse({'detail': 'Upload exceeds 100 MiB'}, status=413)
        response = response or self.get_response(request)
        response['X-Content-Type-Options'] = 'nosniff'
        response['Referrer-Policy'] = 'same-origin'
        response['Cache-Control'] = 'private, no-store'
        return response


def identity(request, service):
    current = service.user_for_cookie(request.COOKIES.get('musicbox_session'))
    if not current:
        raise APIError(401, 'Your listening session expired. Refresh the page to continue.')
    user, csrf = current
    if request.method in {'POST', 'PATCH', 'DELETE', 'PUT'} and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), csrf):
        raise APIError(403, 'Invalid CSRF token')
    return user


def endpoint(pattern, methods, function, service, status=200):
    signature = inspect.signature(function)

    def view(request, **kwargs):
        if request.method not in methods:
            response = JsonResponse({'detail': 'Method not allowed'}, status=405)
            response['Allow'] = ', '.join(methods)
            return response
        try:
            args = dict(kwargs)
            for name, parameter in signature.parameters.items():
                if name in args:
                    continue
                if name == 'request':
                    args[name] = request
                elif name == 'user':
                    args[name] = identity(request, service)
                elif name == 'body':
                    # Pydantic validates the shared JSON contract; Django owns HTTP routing.
                    args[name] = parameter.annotation.model_validate(json.loads(request.body or b'{}'))
                else:
                    value = request.GET.get(name, parameter.default)
                    if name == 'session_id':
                        if value is not None and (not value or len(value)>100):
                            raise APIError(422, 'Session ID must contain 1 to 100 characters')
                    elif value is not None:
                        value = int(value)
                    if name == 'limit' and not 1 <= value <= 50:
                        raise APIError(422, 'Limit must be between 1 and 50')
                    if name == 'seed' and value is not None and not 0 <= value < 2**31:
                        raise APIError(422, 'Seed must be between 0 and 2147483647')
                    args[name] = value
            result = function(**args)
            return result if isinstance(result, HttpResponse) or isinstance(result, StreamingHttpResponse) else JsonResponse(result, status=status)
        except APIError as error:
            return JsonResponse({'detail': error.detail}, status=error.status_code)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
            return JsonResponse({'detail': 'Invalid request data'}, status=422)

    return path(pattern, view)


def audio_response(request, asset):
    size = asset.stat().st_size
    media_type = 'audio/mpeg' if asset.suffix == '.mp3' else 'audio/wav'
    header = request.headers.get('Range')
    if not header:
        response = FileResponse(asset.open('rb'), content_type=media_type)
    else:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
        try:
            if not match or not any(match.groups()) or not size:
                raise ValueError()
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                suffix = int(last)
                if suffix <= 0:
                    raise ValueError()
                start, end = max(0, size - suffix), size - 1
            if start >= size or start > end:
                raise ValueError()
        except ValueError:
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
            response['Accept-Ranges'] = 'bytes'
            return response

        def chunks():
            with asset.open('rb') as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = stream.read(min(64 * 1024, remaining))
                    if not block:
                        break
                    remaining -= len(block)
                    yield block

        response = StreamingHttpResponse(chunks(), status=206, content_type=media_type)
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
        response['Content-Length'] = str(end - start + 1)
    response['Accept-Ranges'] = 'bytes'
    return response
