import { test, expect, Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

async function yueStudio(page: Page, running = false, staleAfterSelect = false) {
  let job: Record<string, any> | null = null;
  let oldJob: Record<string, any> | null = null;
  let savedIndex: number | null = null;
  await page.route('**/api/v1/generation/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/status')) return route.fulfill({ json: { available: true, active: running, queued_count: 0,
      capabilities: { engine: 'yue2', model: 'YuE2-3B-Q8', duration_seconds: 30, takes: 8, languages: ['en','zh'], tested_languages: [], automatic_lyrics: false, audio_reference: false, selection_policy: 'listener', duration_is_maximum: true } } });
    if (path.endsWith('/audio')) return route.fulfill({ contentType: 'audio/wav', body: readFileSync('tests/fixtures/upload.wav') });
    if (path.endsWith('/select')) {
      oldJob = JSON.parse(JSON.stringify(job));
      const index = Number(path.match(/candidates\/(\d+)/)?.[1]);
      job!.selected_candidate_index = index;
      job!.saved_track_id = job!.candidates[index - 1].saved_track_id;
      job!.audio_url = `/api/v1/generation/jobs/yue-batch/candidates/${index}/audio`;
      job!.updated_at++;
      return route.fulfill({ json: { job } });
    }
    if (path.endsWith('/save')) {
      savedIndex = job!.selected_candidate_index;
      job!.saved_track_id = 101;
      job!.candidates[savedIndex! - 1].saved_track_id = 101;
      job!.updated_at++;
      return route.fulfill({ json: { track: { id: 101, playback_status: 'ready' } } });
    }
    if (path.endsWith('/cancel')) {
      job!.status = job!.phase = 'cancelled';
      job!.candidates.forEach((candidate: any) => { if (candidate.status !== 'completed') candidate.status = 'cancelled'; });
      job!.updated_at++;
      return route.fulfill({ json: { job } });
    }
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      job = { id: 'yue-batch', request: { ...body, duration_seconds: 30, engine: 'yue2', takes: 8, seed: 42, references: [] },
        status: running ? 'running' : 'completed', phase: running ? 'rendering' : 'completed', saved_track_id: null,
        created_at: 100, updated_at: 101, elapsed_seconds: null, selected_candidate_index: 1,
        audio_url: '/api/v1/generation/jobs/yue-batch/candidates/1/audio',
        progress: { completed: running ? 2 : 8, finished: running ? 2 : 8, total: 8, active_candidate_index: running ? 3 : null },
        candidates: Array.from({ length: 8 }, (_, i) => ({ index: i + 1, seed: 42 + i, status: !running || i < 2 ? 'completed' : i === 2 ? 'rendering' : 'queued',
          duration_seconds: !running || i < 2 ? 2 : null, saved_track_id: null, audio_url: !running || i < 2 ? `/api/v1/generation/jobs/yue-batch/candidates/${i + 1}/audio` : null })) };
      return route.fulfill({ json: { job } });
    }
    return route.fulfill({ json: { jobs: staleAfterSelect && oldJob ? [oldJob] : job ? [job] : [] } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'AI Studio', exact: true }).click();
  return { savedIndex: () => savedIndex };
}

test('YuE2 exposes eight variations and saves the listener-selected audio on mobile', async ({ page }) => {
  const state = await yueStudio(page);
  await expect(page.getByText('8 variations', { exact: true })).toBeVisible();
  const reference = page.getByTestId('studio-reference').first();
  await reference.getByRole('checkbox').check();
  await expect(page.getByRole('button', { name: /Use audio reference/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Instrumental', exact: true }).click();
  await page.getByLabel('Music prompt').fill('A gentle piano arrangement');
  await page.getByRole('button', { name: 'Generate take', exact: true }).click();
  await expect(page.getByTestId('studio-variation')).toHaveCount(8);
  await page.getByRole('button', { name: 'Preview variation 2', exact: true }).click();
  await expect(page.locator('audio[data-studio-preview]')).toHaveAttribute('src', /candidates\/2\/audio$/);
  await page.getByRole('button', { name: 'Use variation 2', exact: true }).click();
  await page.getByRole('button', { name: 'Save to library', exact: true }).click();
  expect(state.savedIndex()).toBe(2);
  await expect(page.getByRole('link', { name: 'Download WAV' })).toHaveAttribute('href', /candidates\/2\/audio$/);
  await page.screenshot({ path: 'test-results/yue2-studio-desktop.png', fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/yue2-studio-mobile.png', fullPage: true, animations: 'disabled' });
});

test('cancelling an eight-variation batch retains playable completed variations', async ({ page }) => {
  await yueStudio(page, true);
  await page.getByRole('button', { name: 'Instrumental', exact: true }).click();
  await page.getByLabel('Music prompt').fill('Soft piano');
  await page.getByRole('button', { name: 'Generate take', exact: true }).click();
  await expect(page.getByText('2 of 8 ready', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel generation', exact: true }).click();
  await expect(page.getByText('Cancelled', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Preview variation 1', exact: true })).toBeEnabled();
});

test('a stale progress poll cannot revert the chosen variation', async ({ page }) => {
  await yueStudio(page, false, true);
  await page.getByRole('button', { name: 'Instrumental', exact: true }).click();
  await page.getByLabel('Music prompt').fill('Quiet strings');
  await page.getByRole('button', { name: 'Generate take', exact: true }).click();
  await page.getByRole('button', { name: 'Use variation 2', exact: true }).click();
  await page.waitForResponse(response => response.url().endsWith('/generation/jobs') && response.request().method() === 'GET');
  await expect(page.getByRole('link', { name: 'Download WAV' })).toHaveAttribute('href', /candidates\/2\/audio$/);
});
