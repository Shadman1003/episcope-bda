/* Optional browser smoke test. Requires Node and Playwright.
 * Usage: EPISCOPE_URL=http://127.0.0.1:8080 node tests/browser_smoke.cjs
 * Screenshots are written only when EPISCOPE_SCREENSHOT_DIR is set.
 */
const {chromium} = require('playwright');
const path = require('path');
const fs = require('fs');
const {verifyNationalPolicyPlots}=require('./browser_policy_helpers.cjs');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.EPISCOPE_BROWSER_CHANNEL?{channel:process.env.EPISCOPE_BROWSER_CHANNEL}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1100},deviceScaleFactor:1});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(process.env.EPISCOPE_URL||'http://127.0.0.1:8080');
  await page.waitForFunction(()=>Boolean(window.epiScopeState),{timeout:30000});
  const counts=await page.locator('.chart svg').count();if(counts<5||!await page.locator('#warning-body tr').count())throw new Error('Required D3 views did not render');
  if(!(await page.locator('#warning-caption').innerText()).includes('3σ flags use the released detector decision before score rounding'))throw new Error('Default official detector decision is not explained');
  const policyChecks=await verifyNationalPolicyPlots(page,{screenshotDir:process.env.EPISCOPE_SCREENSHOT_DIR});
  const before=await page.evaluate(()=>window.epiScopeState);
  await page.locator('.disease-tab').nth(1).click();
  const afterDisease=await page.evaluate(()=>window.epiScopeState);if(afterDisease.disease===before.disease)throw new Error('Disease did not coordinate');
  await page.locator('#district').selectOption('0');
  const district=await page.evaluate(()=>window.epiScopeState);if(district.district!=='0')throw new Error('District select did not coordinate');
  if(await page.locator('#warning-body tr').count()!==1)throw new Error('Warning table did not follow district selection');
  await page.locator('#threshold').fill('4');await page.locator('#threshold').dispatchEvent('input');
  if((await page.evaluate(()=>window.epiScopeState)).threshold!==4)throw new Error('Threshold slider did not update');
  if(!(await page.locator('#warning-caption').innerText()).includes('What-if flags are approximate comparisons of rounded published scores'))throw new Error('What-if signal approximation is not explained');
  const budget=page.locator('#budget');await budget.fill('0');await budget.dispatchEvent('input');
  await page.locator('#heatmap rect.cell').nth(80).click();
  const afterWeek=await page.evaluate(()=>window.epiScopeState);if(JSON.stringify(afterWeek.range)===JSON.stringify(district.range))throw new Error('Heatmap did not coordinate time range');
  await page.locator('#reset').click();
  const geometry=page.locator('#map .district-shape');await geometry.first().focus();await geometry.first().press('Enter');
  if((await page.evaluate(()=>window.epiScopeState)).district==='all')throw new Error('Choropleth click did not coordinate');
  await page.locator('#reset').click();
  if(process.env.EPISCOPE_SCREENSHOT_DIR){fs.mkdirSync(process.env.EPISCOPE_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.EPISCOPE_SCREENSHOT_DIR,'dashboard-desktop.png'),fullPage:true});}
  await page.setViewportSize({width:390,height:844});
  await page.waitForTimeout(250);
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
  if(overflow)throw new Error('Mobile page overflows horizontally');
  if(process.env.EPISCOPE_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.EPISCOPE_SCREENSHOT_DIR,'dashboard-mobile.png'),fullPage:true});
  if(errors.length)throw new Error(errors.join('\n'));
  console.log(JSON.stringify({ok:true,svg_views:counts,checks:['disease','district','threshold','official_default_approximate_what_if_labels','budget','heatmap_time','choropleth_keyboard','responsive_390px',...policyChecks,'no_browser_exceptions']},null,2));
  await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
