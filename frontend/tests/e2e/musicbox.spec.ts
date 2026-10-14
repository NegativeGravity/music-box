import { test, expect } from '@playwright/test';

test('library, playback, feedback, focus and search work', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Library', exact: true })).toBeVisible();
  await expect(page.locator('[data-testid="track-card"]').first()).toBeVisible();
  await page.locator('[data-testid="track-play"]').first().click();
  await expect(page.locator('audio')).toHaveJSProperty('paused', false);
  await page.locator('[data-testid="track-like"]').first().click();
  await expect(page.locator('[data-testid="track-like"]').first()).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Personalize', exact: true }).click();
  await page.getByRole('button', { name: 'Focus', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Focus', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Library', exact: true }).click();
  await page.getByPlaceholder('Search tracks or artists…').fill('zzzz-no-match');
  await expect(page.getByText('No tracks found')).toBeVisible();
  await page.getByPlaceholder('Search tracks or artists…').fill('');
  await expect(page.locator('[data-testid="track-card"]').first()).toBeVisible();
});

test('private upload is playable and can be deleted', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Upload track', exact: true }).first().click();
  await page.locator('input[type=file]').setInputFiles('tests/fixtures/upload.wav');
  await page.getByLabel('Track title').fill('E2E private upload');
  await page.getByRole('button', { name: 'Add to library', exact: true }).click();
  await page.getByRole('button', { name: 'Library', exact: true }).click();
  const card = page.locator('[data-testid="track-card"]').filter({ hasText: 'E2E private upload' });
  await expect(card).toBeVisible();
  await expect(card.getByTestId('track-play')).toBeEnabled({ timeout: 45000 });
  await card.getByTestId('track-play').click();
  await expect(page.locator('audio')).toHaveJSProperty('paused', false);
  await card.getByRole('button', { name: 'Track options' }).click();
  await page.getByRole('button', { name: 'Delete from library', exact: true }).click();
  await expect(card).toHaveCount(0);
});

test('responsive player stays inside viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('[data-testid="track-card"]').first()).toBeVisible();
  const widths = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(widths[0]).toBeLessThanOrEqual(widths[1]);
});

test('exposure is recorded once while the audio player rerenders', async ({page}) => {
  const exposures: {event_id:string,track_id:number,request_id:string}[]=[];
  page.on('request',request=>{
    if(request.url().endsWith('/events/batch')){
      const body=request.postDataJSON();
      exposures.push(...body.events.filter((event: {event_type:string})=>event.event_type==='exposure'));
    }
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Personalize', exact: true }).click();
  await page.getByTestId('track-play').first().click();
  await expect(page.locator('audio')).toHaveJSProperty('paused',false);
  await page.waitForTimeout(2500);
  expect(exposures.length).toBeGreaterThan(0);
  await page.getByLabel('Volume').fill('0.3');
  await page.waitForTimeout(1500);
  const eventIds=exposures.map(e=>e.event_id);
  const uniqueEvents=exposures.filter((e,i)=>eventIds.indexOf(e.event_id)===i);
  const keys=uniqueEvents.map(e=>`${e.request_id}:${e.track_id}`);
  expect(new Set(keys).size).toBe(keys.length);
});

test('private uploads stay isolated without login and survive reload', async ({ page, browser }) => {
  await page.goto('/');
  await page.getByTestId('track-card').first().waitFor();
  await expect(page.locator('input[type=password], input[type=email]')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /log in|sign in|register|sign up/i })).toHaveCount(0);
  await page.getByRole('button', { name: 'Upload track', exact: true }).first().click();
  await page.locator('input[type=file]').setInputFiles('tests/fixtures/upload.wav');
  const title = `PRIVATE BROWSER TRACK ${Date.now()}`;
  await page.getByLabel('Track title').fill(title);
  await page.getByRole('button', { name: 'Add to library', exact: true }).click();
  const card = page.getByTestId('track-card').filter({ hasText: title });
  await expect(card.getByTestId('track-play')).toBeEnabled({ timeout: 45000 });
  await page.reload();
  await page.getByRole('button', { name: 'Library', exact: true }).click();
  await expect(card).toBeVisible();
  const otherContext = await browser.newContext();
  try {
    const other = await otherContext.newPage();
    await other.goto(process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:3000');
    await other.getByRole('button', { name: 'Library', exact: true }).click();
    await expect(other.getByTestId('track-card').filter({ hasText: title })).toHaveCount(0);
  } finally { await otherContext.close(); }
  await card.getByRole('button', { name: 'Track options' }).click();
  await page.getByRole('button', { name: 'Delete from library', exact: true }).click();
  await expect(card).toHaveCount(0);
});

test('English interface uses a left sidebar and a working mobile menu', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('track-card').first().waitFor();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  expect(await page.locator('body').innerText()).not.toMatch(/[\u0600-\u06ff]/);
  const sidebar = await page.locator('.sidebar').boundingBox();
  expect(sidebar?.x).toBe(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Open menu' }).click();
  await expect(page.getByRole('button', { name: 'Library', exact: true })).toBeInViewport();
  await page.getByRole('button', { name: 'Library', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Open menu' })).toBeVisible();
  await page.getByRole('button', { name: 'Playback queue', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Close dialog' }).click();
});

test('a deleted-track event does not poison the playback outbox',async ({page})=>{
  await page.goto('/');
  await page.getByTestId('track-card').first().waitFor();
  await page.evaluate(async ()=>{
    const auth=await (await fetch('/api/v1/session')).json();
    localStorage.setItem(`musicbox.events.${auth.user.id}`,JSON.stringify([{event_id:crypto.randomUUID(),track_id:9999999,event_type:'exposure',request_id:null,session_id:'offline-test',position:null,occurred_at:new Date().toISOString(),playback_origin:'library'}]));
  });
  await page.getByTestId('track-play').first().click();
  await expect.poll(()=>page.evaluate(async ()=>{
    const auth=await (await fetch('/api/v1/session')).json();
    return JSON.parse(localStorage.getItem(`musicbox.events.${auth.user.id}`)||'[]').length;
  })).toBe(0);
});
