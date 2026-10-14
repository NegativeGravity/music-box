import { test, expect, Page } from '@playwright/test';

async function startTrack(page: Page) {
  const context=page.waitForResponse(r=>r.url().includes('/recommendations/discovery') && r.url().includes('current_id=') && r.status()===200);
  await page.getByTestId('track-card').first().getByRole('button',{name:/^Play /}).click();
  await expect.poll(()=>page.locator('audio').evaluate((node:HTMLAudioElement)=>!node.paused)).toBe(true);
  const result=await (await context).json();
  await expect(page.locator('.smart-next h4')).toHaveText(result.next_items[0].title);
}

async function settleReply(page: Page) {
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
}

test('taste mixes can be saved and automatic continuation uses the next recommendation', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('track-card').first().waitFor();
  await page.getByTestId('track-like').first().click();
  await page.getByRole('button', { name: 'Personalize', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Your taste mixes' })).toBeVisible();
  const group=page.getByTestId('taste-mix').first();
  await expect(group).toBeVisible();
  await group.getByRole('button', { name: 'Save mix', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Mix saved');
  const lists=await page.evaluate(async () => (await (await fetch('/api/v1/playlists')).json()).playlists);
  expect(lists[0].track_ids.length).toBeGreaterThan(0);
  await startTrack(page);
  const before=await page.locator('audio').getAttribute('src');
  const response=page.waitForResponse(r => r.url().includes('/recommendations/discovery') && r.url().includes('current_id=') && r.status()===200);
  await page.locator('audio').evaluate(node => node.dispatchEvent(new Event('ended')));
  const recommendation=await (await response).json();
  await expect(page.locator('audio')).toHaveAttribute('src',`/api/v1/tracks/${recommendation.next_items[0].id}/stream`);
  expect(await page.locator('audio').getAttribute('src')).not.toBe(before);
});

test('disabling smart autoplay while a next request is pending prevents a late switch', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('track-card').first().waitFor();
  await page.getByRole('button', { name: 'Personalize', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Smart autoplay', exact: true })).toHaveAttribute('aria-pressed','true');
  await startTrack(page);
  const before=await page.locator('audio').getAttribute('src');
  let release!:()=>void;let reached!:()=>void;
  const gate=new Promise<void>(resolve => {release=resolve;});
  const started=new Promise<void>(resolve => {reached=resolve;});
  await page.route('**/api/v1/recommendations/discovery?**',async route => {reached();await gate;await route.continue();});
  const completed=page.waitForResponse(r=>r.url().includes('/recommendations/discovery') && r.status()===200);
  await page.locator('audio').evaluate(node => node.dispatchEvent(new Event('ended')));
  await started;
  const patch=page.waitForResponse(r=>r.url().includes('/preferences') && r.request().method()==='PATCH');
  await page.getByRole('button', { name: 'Smart autoplay', exact: true }).click();
  await patch;
  release();
  await expect(page.getByRole('button', { name: 'Smart autoplay', exact: true })).toHaveAttribute('aria-pressed','false');
  await (await completed).finished();await settleReply(page);
  expect(await page.locator('audio').getAttribute('src')).toBe(before);
});

for (const action of ['manual track','pause','personalize off'] as const) {
  test(`a pending autoplay reply respects ${action}`, async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('track-card').first().waitFor();
    await page.getByRole('button', { name: 'Personalize', exact: true }).click();
    await startTrack(page);
    let release!:()=>void;let reached!:()=>void;
    const gate=new Promise<void>(resolve => {release=resolve;});
    const started=new Promise<void>(resolve => {reached=resolve;});
    await page.route('**/api/v1/recommendations/discovery?**',async route=>{reached();await gate;await route.continue();});
    const completed=page.waitForResponse(r=>r.url().includes('/recommendations/discovery') && r.status()===200);
    await page.locator('audio').evaluate(node=>node.dispatchEvent(new Event('ended')));
    await started;
    if(action==='manual track') await page.getByTestId('track-card').nth(2).getByRole('button',{name:/^Play /}).click();
    else if(action==='pause') await page.getByRole('button',{name:'Pause',exact:true}).click();
    else await page.getByRole('button',{name:'Personalize',exact:true}).click();
    const selected=await page.locator('audio').getAttribute('src');
    release();
    await (await completed).finished();await settleReply(page);
    expect(await page.locator('audio').getAttribute('src')).toBe(selected);
    if(action==='pause') expect(await page.locator('audio').evaluate((node:HTMLAudioElement)=>node.paused)).toBe(true);
    if(action==='personalize off') await expect(page.getByRole('button',{name:'Personalize',exact:true})).toHaveAttribute('aria-pressed','false');
  });
}

test('playing a taste mix honors its order before smart continuation',async ({page})=>{
  await page.goto('/');await page.getByTestId('track-card').first().waitFor();
  await page.getByTestId('track-like').first().click();
  const response=page.waitForResponse(r=>r.url().includes('/recommendations/discovery') && r.status()===200);
  await page.getByRole('button',{name:'Personalize',exact:true}).click();
  const data=await (await response).json();
  expect(data.interest_groups[0].items.length).toBeGreaterThan(1);
  await page.getByTestId('taste-mix').first().getByRole('button',{name:'Play mix',exact:true}).click();
  await expect.poll(()=>page.locator('audio').evaluate((node:HTMLAudioElement)=>!node.paused)).toBe(true);
  await page.locator('audio').evaluate(node=>node.dispatchEvent(new Event('ended')));
  await expect(page.locator('audio')).toHaveAttribute('src',`/api/v1/tracks/${data.interest_groups[0].items[1].id}/stream`);
});

test('Personalize opt-out cancels continuation before a delayed preference PATCH completes',async({page})=>{
  await page.goto('/');await page.getByTestId('track-card').first().waitFor();
  await page.getByRole('button',{name:'Personalize',exact:true}).click();
  await startTrack(page);
  const before=await page.locator('audio').getAttribute('src');
  let releaseNext!:()=>void;let reachedNext!:()=>void;let releasePatch!:()=>void;
  const nextGate=new Promise<void>(resolve=>{releaseNext=resolve;});
  const started=new Promise<void>(resolve=>{reachedNext=resolve;});
  const patchGate=new Promise<void>(resolve=>{releasePatch=resolve;});
  await page.route('**/api/v1/recommendations/discovery?**',async route=>{reachedNext();await nextGate;await route.continue();});
  await page.route('**/api/v1/preferences',async route=>{await patchGate;await route.continue();});
  const completed=page.waitForResponse(r=>r.url().includes('/recommendations/discovery') && r.status()===200);
  await page.locator('audio').evaluate(node=>node.dispatchEvent(new Event('ended')));await started;
  await page.getByRole('button',{name:'Personalize',exact:true}).click();
  releaseNext();await (await completed).finished();await settleReply(page);
  try {expect(await page.locator('audio').getAttribute('src')).toBe(before);} finally {releasePatch();}
  await expect(page.getByRole('button',{name:'Personalize',exact:true})).toHaveAttribute('aria-pressed','false');
});
