import { test, expect, Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

// Explicit browser contract fixtures; the separate real-engine flow uses Django.
async function studio(page: Page, available = true, completed = false, uncertain = false, responseDelay = 120) {
  const calls: { key: string; body: Record<string, unknown> }[] = [];
  const jobs: Record<string, unknown>[] = [];
  await page.route('**/api/v1/generation/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/status')) return route.fulfill({ json: { available, active: false, queued_count: 0, capabilities: { duration_seconds: 20, takes: 1, languages: ['en','es'], tested_languages: ['en'], automatic_lyrics: false } } });
    if (path.endsWith('/audio')) return route.fulfill({ contentType: 'audio/wav', body: readFileSync('tests/fixtures/upload.wav') });
    if (path.endsWith('/save')) {
      jobs[0].saved_track_id = 1;
      return route.fulfill({ json: { track: { id: 1, playback_status: 'ready' } } });
    }
    if (route.request().method() === 'POST') {
      const key = route.request().headers()['idempotency-key'];
      const body = route.request().postDataJSON();
      calls.push({ key, body });
      if (!jobs.length) jobs.push({ id: 'contract-take', request: { ...body, duration_seconds: 20, seed: 42, references: [] }, status: completed ? 'completed' : 'running', phase: completed ? 'completed' : 'rendering', saved_track_id: null, created_at: Date.now()/1000, elapsed_seconds: completed ? 195 : null, audio_url: completed ? '/api/v1/generation/jobs/contract-take/audio' : null });
      if (uncertain && calls.length === 1) return route.abort('failed');
      if (responseDelay) await new Promise(resolve => setTimeout(resolve, responseDelay));
      return route.fulfill({ json: { job: jobs[0] } });
    }
    return route.fulfill({ json: { jobs } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'AI Studio', exact: true }).click();
  return { calls, jobs };
}

test('vocal lyrics, primary inspiration and instrumental requests are explicit', async ({ page }) => {
  const { calls } = await studio(page);
  await page.getByLabel('Music prompt').fill('Warm indie pop with soft drums');
  await expect(page.getByRole('button', { name: 'Generate take', exact: true })).toBeDisabled();
  await page.getByLabel('Lyrics', { exact: true }).fill('[Verse]\nA new day begins');
  const first = page.getByTestId('studio-reference').first();
  await first.getByRole('checkbox').check();
  await first.getByRole('button', { name: /Use audio/ }).click();
  await page.getByRole('button', { name: 'Generate take', exact: true }).dblclick();
  await expect(page.getByText('Rendering audio', { exact: true })).toBeVisible();
  expect(calls).toHaveLength(1);
  expect(calls[0].body.mode).toBe('vocal');
  expect(calls[0].body.primary_reference_track_id).toBe(calls[0].body.reference_track_ids && (calls[0].body.reference_track_ids as number[])[0]);
  await page.getByRole('button', { name: 'Instrumental', exact: true }).click();
  await expect(page.getByLabel('Lyrics', { exact: true })).toHaveCount(0);
  await expect(page.getByText('20 seconds', { exact: true })).toBeVisible();
});

test('takes survive refresh and uncertain submissions keep one request key', async ({ page }) => {
  const { calls } = await studio(page, true, false, true);
  await page.getByRole('button', { name: 'Instrumental', exact: true }).click();
  await page.getByLabel('Music prompt').fill('A sparse piano miniature');
  await page.getByRole('button', { name: 'Generate take', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Retry same request', exact: true })).toBeVisible();
  await page.getByLabel('Music prompt').fill('Edited while the outcome is unknown');
  await page.getByRole('button', { name: 'Retry same request', exact: true }).click();
  expect(calls).toHaveLength(2);
  expect(calls[0].key).toBe(calls[1].key);
  expect(calls[0].body).toEqual(calls[1].body);
  await page.reload();
  await page.getByRole('button', { name: 'AI Studio', exact: true }).click();
  await expect(page.getByText('A sparse piano miniature', { exact: true })).toBeVisible();
  await expect(page.getByText('Rendering audio', { exact: true })).toBeVisible();
});

test('offline engine explains availability and blocks generation', async ({ page }) => {
  await studio(page, false);
  await expect(page.getByText('Local engine offline', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Instrumental', exact: true }).click();
  await page.getByLabel('Music prompt').fill('Quiet ambient synths');
  await expect(page.getByRole('button', { name: 'Generate take', exact: true })).toBeDisabled();
});

test('completed native take can preview, download and save once on mobile', async ({ page }) => {
  await studio(page, true, true);
  await page.getByRole('button', { name: 'Library', exact: true }).click();
  await page.getByTestId('track-play').first().click();
  await expect(page.locator('audio').first()).toHaveJSProperty('paused', false);
  await page.getByRole('button', { name: 'AI Studio', exact: true }).click();
  await page.getByRole('button', { name: 'Instrumental', exact: true }).click();
  await page.getByLabel('Music prompt').fill('Quiet ambient synths');
  await page.getByRole('button', { name: 'Generate take', exact: true }).click();
  await page.getByRole('button', { name: 'Preview take', exact: true }).click();
  await expect(page.locator('audio[data-studio-preview]')).toHaveJSProperty('paused', false);
  await expect(page.locator('audio').first()).toHaveJSProperty('paused', true);
  await expect(page.getByTestId('studio-waveform').locator('i')).toHaveCount(64);
  await page.getByRole('button', { name: 'Resume playback', exact: true }).click();
  await expect(page.locator('audio[data-studio-preview]')).toHaveJSProperty('paused', true);
  await expect(page.getByRole('link', { name: 'Download WAV' })).toHaveAttribute('href', /\/audio$/);
  await page.getByRole('button', { name: 'Save to library', exact: true }).click();
  await expect(page.getByText('Saved to your library', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.getByLabel('Music prompt')).toBeVisible();
});

test('an uncertain request survives reload and is recoverable with an offline engine', async ({ page }) => {
  const { calls } = await studio(page, true, false, true);
  await page.getByRole('button', { name: 'Instrumental', exact: true }).click();
  await page.getByLabel('Music prompt').fill('Recover this same piano request');
  await page.getByRole('button', { name: 'Generate take', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Retry same request', exact: true })).toBeEnabled();
  await page.route('**/api/v1/generation/status', route => route.fulfill({ json: { available: false, active: false, queued_count: 0, capabilities: { duration_seconds: 20, takes: 1, languages: ['en'], tested_languages: ['en'], automatic_lyrics: false } } }));
  await page.reload();
  await page.getByRole('button', { name: 'AI Studio', exact: true }).click();
  await expect(page.getByText('Local engine offline', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Retry same request', exact: true }).click({ timeout: 3000 });
  expect(calls).toHaveLength(2);
  expect(calls[0]).toEqual(calls[1]);
});

test('a double-click with a fast response creates one take but a later deliberate click creates another', async ({ page }) => {
  const { calls } = await studio(page, true, false, false, 0);
  await page.getByRole('button', { name: 'Instrumental', exact: true }).click();
  await page.getByLabel('Music prompt').fill('A single deliberate piano take');
  await page.getByRole('button', { name: 'Generate take', exact: true }).dblclick({ delay: 180 });
  await expect(page.getByText('Rendering audio', { exact: true })).toBeVisible();
  expect(calls).toHaveLength(1);
  await page.waitForTimeout(650); // End this native multi-click gesture before explicitly creating another.
  await page.getByRole('button', { name: 'Generate another take', exact: true }).click();
  await expect.poll(() => calls.length).toBe(2);
  expect(calls[0].key).not.toBe(calls[1].key);
});
