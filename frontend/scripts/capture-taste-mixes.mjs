import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const browser=await chromium.launch({channel:'chrome',headless:true});
try {
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  await page.goto('http://127.0.0.1:3000');
  await page.getByTestId('track-card').first().waitFor();
  // An isolated preview profile; no existing listener preferences are touched.
  await page.evaluate(async()=>{
    const state=await (await fetch('/api/v1/session')).json();
    const library=await (await fetch('/api/v1/library/tracks')).json();
    const real=library.tracks.filter(t=>t.source==='catalog');
    const selected=[real.find(t=>t.title.toLowerCase().includes('hit') && t.artist.toLowerCase().includes('pac')) || real[0],real.find(t=>t.title==='Summertime') || real.at(-1)];
    for(const track of selected.filter(Boolean)) {
      const response=await fetch('/api/v1/feedback',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf_token},body:JSON.stringify({track_id:track.id,signal:'like',event_id:crypto.randomUUID()})});
      if(!response.ok) throw new Error('Preview like failed');
    }
  });
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  const response=page.waitForResponse(r=>r.url().includes('/recommendations/discovery') && r.status()===200);
  await page.getByRole('button',{name:'Personalize',exact:true}).click();
  const result=await (await response).json();
  await page.getByTestId('taste-mix').first().waitFor();
  await page.evaluate(()=>document.fonts.ready);
  await mkdir('../docs/implementation/previews',{recursive:true});
  await page.locator('.taste-discovery').evaluate(node=>node.scrollIntoView({block:'start'}));
  await page.screenshot({path:'../docs/implementation/previews/taste-mixes-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=0);
  await page.locator('.taste-discovery').evaluate(node=>node.scrollIntoView({block:'start'}));
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)) throw new Error('Mobile overflow');
  await page.screenshot({path:'../docs/implementation/previews/taste-mixes-mobile.png'});
  console.log(JSON.stringify({interests:result.interest_groups.map(g=>g.title),candidate_count:result.candidate_count}));
} finally {await browser.close();}
