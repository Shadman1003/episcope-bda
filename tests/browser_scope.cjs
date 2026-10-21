/* Optional role-bound browser check against a token-configured local server.
 * EPISCOPE_DISTRICT_TOKEN supplies a real or temporary test district token.
 * The token is never printed or included in screenshots.
 */
const {chromium}=require('playwright');
const {verifyDistrictPolicyHidden}=require('./browser_policy_helpers.cjs');
(async()=>{
  if(!process.env.EPISCOPE_DISTRICT_TOKEN)throw new Error('Set EPISCOPE_DISTRICT_TOKEN to a configured district principal.');
  const browser=await chromium.launch({headless:true,...(process.env.EPISCOPE_BROWSER_CHANNEL?{channel:process.env.EPISCOPE_BROWSER_CHANNEL}:{})});
  const page=await browser.newPage({viewport:{width:1100,height:900}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(process.env.EPISCOPE_URL||'http://127.0.0.1:8080');
  let nationalPolicyLoaded=false;
  if(process.env.EPISCOPE_NATIONAL_TOKEN){
    await page.locator('#access-button').click();await page.locator('#token-input').fill(process.env.EPISCOPE_NATIONAL_TOKEN);await page.locator('#token-submit').click();
    await page.waitForFunction(()=>window.epiScopeState?.scope==='national');
    await page.locator('#policy-checks > summary').click();
    await page.waitForFunction(()=>document.querySelectorAll('#detection-policy .detection-point').length===14);
    if(await page.locator('#allocation-policy .allocation-policy-point').count()!==24)throw new Error('National scenario grid did not load before scope transition.');
    nationalPolicyLoaded=true;
  }
  await page.locator('#access-button').click();
  await page.locator('#token-input').fill(process.env.EPISCOPE_DISTRICT_TOKEN);
  const responsePromise=page.waitForResponse(response=>response.url().endsWith('/api/data')&&response.status()===200);
  await page.locator('#token-submit').click();
  const response=await responsePromise,payload=await response.json();
  await page.waitForFunction(()=>window.epiScopeState?.scope==='district');
  if(payload.districts.length!==1)throw new Error('Server returned more than one district.');
  for(const table of ['observed','forecasts','signals','allocations'])if(payload[table].some(row=>row[0]!==0))throw new Error('District table was not remapped consistently.');
  if(await page.locator('#district option').count()!==1)throw new Error('District picker exposed another scope.');
  if(await page.locator('#clear-district').isVisible())throw new Error('National scope control visible to district principal.');
  if(await page.locator('#warning-body tr').count()!==1)throw new Error('Warning table did not stay in district scope.');
  if((await page.locator('h1').textContent()).startsWith('National'))throw new Error('Heading did not follow district scope.');
  const districtPolicyCheck=await verifyDistrictPolicyHidden(page,payload);
  await page.locator('#reset').click();
  if((await page.evaluate(()=>window.epiScopeState)).district!=='0')throw new Error('Reset escaped district scope.');
  const staticResponse=await page.request.get(new URL('/data/dashboard.json',page.url()).href);if(staticResponse.status()!==404)throw new Error('Static release could bypass API scope.');
  if(errors.length)throw new Error(errors.join('\n'));
  console.log(JSON.stringify({ok:true,scope:payload.access.role,districts_returned:payload.districts.length,national_to_district_policy_transition:nationalPolicyLoaded,checks:['token_ui','server_scope','consistent_indices','district_controls','reset_scope',districtPolicyCheck,...(nationalPolicyLoaded?['national_to_district_policy_dom_cleared']:[]),'static_release_blocked','no_browser_exceptions']},null,2));
  await browser.close();
})().catch(error=>{console.error(error.message);process.exit(1);});
