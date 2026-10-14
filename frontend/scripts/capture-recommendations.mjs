import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto('http://127.0.0.1:3000');
  await page.getByTestId('track-card').first().waitFor();
  const response = page.waitForResponse(r => r.url().includes('/recommendations/for-you') && r.status() === 200);
  await page.getByRole('button', { name: 'Personalize', exact: true }).click();
  const result = await (await response).json();
  await page.locator('.recommendation-note').waitFor();
  await page.evaluate(() => document.fonts.ready);
  await mkdir('../docs/implementation/previews', { recursive: true });
  await page.locator('.section-heading').evaluate(node => node.scrollIntoView({ block: 'start' }));
  await page.screenshot({ path: '../docs/implementation/previews/recommendations-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 0);
  await page.locator('.section-heading').evaluate(node => node.scrollIntoView({ block: 'start' }));
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile content overflows the viewport');
  await page.screenshot({ path: '../docs/implementation/previews/recommendations-mobile.png' });
  console.log(JSON.stringify({ candidate_count: result.candidate_count, scored_count: result.scored_count, returned_count: result.returned_count, strategy: result.strategy }));
} finally {
  await browser.close();
}
