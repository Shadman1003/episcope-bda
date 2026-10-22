/* Evidence-only integration check. Uses the actual exported dashboard release. */
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('assert/strict');
const crypto=require('crypto');
const {verifyDistrictPolicyHidden}=require('./browser_policy_helpers.cjs');
(async()=>{
  const base=process.env.EPISCOPE_URL||'http://127.0.0.1:8765';
  const roleBase=process.env.EPISCOPE_ROLE_URL||'http://127.0.0.1:8766';
  if(!process.env.EPISCOPE_SCREENSHOT_DIR)throw new Error('Set EPISCOPE_SCREENSHOT_DIR to an evidence directory.');
  const evidence=path.resolve(process.env.EPISCOPE_SCREENSHOT_DIR);fs.mkdirSync(evidence,{recursive:true});
  const bytes=fs.readFileSync(process.env.EPISCOPE_DASHBOARD_FILE||path.resolve(__dirname,'../dashboard/data/dashboard.json'));
  const release=JSON.parse(bytes), checks=[];
  const browser=await chromium.launch({headless:true,...(process.env.EPISCOPE_BROWSER_CHANNEL?{channel:process.env.EPISCOPE_BROWSER_CHANNEL}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1100},deviceScaleFactor:1});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const responsePromise=page.waitForResponse(r=>r.url()===base+'/api/data'&&r.status()===200);
  await page.goto(base,{waitUntil:'networkidle'});
  const published=await (await responsePromise).json();
  await page.waitForFunction(()=>Boolean(window.epiScopeState));
  assert.equal(published.generated_at,release.generated_at);
  assert.equal(published.districts.length,release.districts.length);
  assert.equal(published.observed.length,release.observed.length);
  const state=()=>page.evaluate(()=>window.epiScopeState);
  const initial=await state();
  assert.equal(initial.scope,'national');
  for(const id of ['map','series','heatmap','allocation'])assert.equal(await page.locator(`#${id} svg[role=img]`).count(),1,id);
  assert.equal(await page.locator('#map .district-shape').count(),release.districts.length);
  assert.ok(await page.locator('#heatmap rect.cell').count()>100);
  assert.ok(await page.locator('#allocation g.bar').count()>0);
  assert.ok(await page.locator('#warning-body tr').count()>0);
  checks.push('five_views_render_with_actual_release');
  await page.screenshot({path:path.join(evidence,'dashboard-desktop-overview.png')});
  await page.screenshot({path:path.join(evidence,'dashboard-national-desktop.png'),fullPage:true});
  for(const [id,name] of [['map','geography'],['series','trajectory'],['heatmap','seasonality'],['allocation','resource-planning'],['warnings','early-warning']]){
    const card=id==='warnings'?page.locator('#warnings'):page.locator(`#${id}`).locator('..');
    await card.screenshot({path:path.join(evidence,`dashboard-view-${name}.png`)});
  }
  const first=page.locator('#map .district-shape').first();
  // Districts can have concave or multipart shapes; their bounding-box centre
  // can be outside their painted area. Move to an actual interior screen point.
  await first.scrollIntoViewIfNeeded();
  const inside=await first.evaluate(shape=>{
    const box=shape.getBBox();
    for(let y=1;y<20;y++)for(let x=1;x<20;x++){
      const point=new DOMPoint(box.x+box.width*x/20,box.y+box.height*y/20);
      if(shape.isPointInFill(point))return point.matrixTransform(shape.getScreenCTM()).toJSON();
    }
    throw new Error('No interior district point found');
  });
  await page.mouse.move(inside.x,inside.y);assert.equal(await page.locator('#tooltip').isVisible(),true);
  await first.focus();await first.press('Enter');
  assert.notEqual((await state()).district,'all');
  assert.equal(await page.locator('#warning-body tr').count(),1);
  assert.notEqual(await page.locator('#selection-pill').innerText(),'All districts');
  assert.equal(await page.locator('#map .district-shape.selected').count(),1);
  checks.push('choropleth_tooltip_keyboard_links_other_views');
  await page.locator('#reset').click();
  const beforeBrush=await state(),nav=await page.locator('#navigator .brush .overlay').boundingBox();
  await page.mouse.move(nav.x+nav.width*.52,nav.y+nav.height*.5);
  await page.mouse.down();await page.mouse.move(nav.x+nav.width*.8,nav.y+nav.height*.5,{steps:12});await page.mouse.up();
  assert.notDeepEqual((await state()).range,beforeBrush.range);
  checks.push('trajectory_navigator_brush_links_date_range');
  await page.locator('#reset').click();
  const beforeZoom=await state(),plot=await page.locator('#series svg > rect').boundingBox();
  await page.mouse.move(plot.x+plot.width*.6,plot.y+plot.height*.5);await page.mouse.wheel(0,-250);await page.waitForTimeout(150);
  assert.notDeepEqual((await state()).range,beforeZoom.range);
  checks.push('trajectory_mouse_wheel_zoom');
  await page.locator('#reset').click();
  const beforeHeat=await state(),cell=page.locator('#heatmap rect.cell').nth(80);
  await cell.focus();await cell.press('Enter');
  assert.notDeepEqual((await state()).range,beforeHeat.range);
  assert.match(await page.locator('#scenario-note').innerText(),/Historical time selection/);
  checks.push('seasonal_keyboard_cell_links_all_views_and_historical_label');
  await page.locator('#reset').click();
  const beforeBudget=await state();await page.locator('#budget').fill('0');await page.locator('#budget').dispatchEvent('input');
  assert.notEqual((await state()).budget,beforeBudget.budget);
  const beforeEquity=await state();await page.locator('#equity').fill('0');await page.locator('#equity').dispatchEvent('input');
  assert.notEqual((await state()).equity,beforeEquity.equity);
  const resourceOptions=await page.locator('#resource option').count();
  for(let i=0;i<resourceOptions&&!await page.locator('#allocation g.bar').count();i++)await page.locator('#resource').selectOption(String(i));
  const bar=page.locator('#allocation g.bar').first();assert.ok(await bar.count());
  // The group spans stock, demand and baseline markers; its box centre can be
  // an unpainted gap. Hover a real painted marker so the user event bubbles.
  await bar.locator('rect').last().hover();assert.equal(await page.locator('#tooltip').isVisible(),true);await bar.focus();await bar.press('Enter');
  assert.notEqual((await state()).district,'all');assert.equal(await page.locator('#warning-body tr').count(),1);
  checks.push('allocation_budget_equity_resource_and_keyboard_link');
  await page.locator('#reset').click();
  const row=page.locator('#warning-body tr').first();const district=await row.getAttribute('data-district');
  await row.focus();await row.press('Enter');assert.equal((await state()).district,district);
  await page.locator('#threshold').fill('4');await page.locator('#threshold').dispatchEvent('input');assert.equal((await state()).threshold,4);
  checks.push('warning_row_keyboard_link_and_detection_threshold');
  await page.locator('#reset').click();await page.locator('.disease-tab').nth(1).click();
  assert.notEqual((await state()).disease,initial.disease);checks.push('disease_tabs_coordinate');
  // Reopen the default state for comparable desktop/mobile evidence.
  await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>Boolean(window.epiScopeState));
  assert.deepEqual(await state(),initial);
  for(const width of [390,768,1440]){
    await page.setViewportSize({width,height:width===390?844:1100});await page.waitForTimeout(200);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false,`horizontal overflow at ${width}`);
    if(width===390){
      await page.evaluate(()=>window.scrollTo({top:0,left:0,behavior:'instant'}));
      await page.screenshot({path:path.join(evidence,'dashboard-mobile-overview.png')});
      await page.screenshot({path:path.join(evidence,'dashboard-national-mobile.png'),fullPage:true});
    }
  }
  checks.push('responsive_390_768_1440_no_horizontal_overflow');
  const request=page.request;
  assert.equal((await request.get(roleBase+'/api/data')).status(),401);
  assert.equal((await request.get(roleBase+'/api/data',{headers:{Authorization:'Bearer invalid'}})).status(),401);
  const auth={Authorization:'Bearer '+process.env.EPISCOPE_DISTRICT_TOKEN};
  const districtResponse=await request.get(roleBase+'/api/data?district_id=64',{headers:auth});assert.equal(districtResponse.status(),200);
  const scoped=await districtResponse.json();assert.equal(scoped.districts.length,1);assert.equal(scoped.districts[0].district_id,'01');
  for(const table of ['observed','forecasts','signals','allocations'])assert.ok(scoped[table].every(r=>r[0]===0),table);
  assert.equal(scoped.geography.features.length,1);assert.equal(scoped.geography.features[0].properties.district_id,'01');
  assert.ok(scoped.calendar.every(r=>r[0]===scoped.districts[0].division));
  for(const table of ['quality','tradeoff','scenarios'])assert.deepEqual(scoped[table],[]);
  assert.equal(districtResponse.headers()['cache-control'],'no-store');
  assert.ok(districtResponse.headers()['content-security-policy'].includes("frame-ancestors 'none'"));
  for(const endpoint of ['/data/dashboard.json','/data/manifest.json','/%2e%2e/data/dashboard.json','/serving/server.py','/data/'])assert.equal((await request.get(roleBase+endpoint,{headers:auth})).status(),404,endpoint);
  const national=await request.get(roleBase+'/api/data',{headers:{Authorization:'Bearer '+process.env.EPISCOPE_NATIONAL_TOKEN}});assert.equal(national.status(),200);assert.equal((await national.json()).districts.length,release.districts.length);
  checks.push('server_401_invalid_token_static_and_traversal_blocked','server_district_all_tables_geometry_calendar_scoped','server_query_cannot_escape_scope_security_headers','server_national_token_preserves_national_scope');
  const scopedPage=await browser.newPage({viewport:{width:1440,height:1100},deviceScaleFactor:1});scopedPage.on('pageerror',e=>errors.push(e.message));
  await scopedPage.goto(roleBase);await scopedPage.locator('#access-button').click();await scopedPage.locator('#token-input').fill(process.env.EPISCOPE_DISTRICT_TOKEN);await scopedPage.locator('#token-submit').click();
  await scopedPage.waitForFunction(()=>window.epiScopeState?.scope==='district');
  assert.equal(await scopedPage.locator('#district option').count(),1);assert.equal(await scopedPage.locator('#clear-district').isVisible(),false);assert.equal(await scopedPage.locator('#warning-body tr').count(),1);
  checks.push(await verifyDistrictPolicyHidden(scopedPage,scoped));
  await scopedPage.screenshot({path:path.join(evidence,'dashboard-district-scope-desktop.png'),fullPage:true});
  assert.deepEqual(errors,[]);checks.push('district_browser_scope_and_no_browser_exceptions');
  const result={ok:true,evidence_type:'Actual exported gold release; encounter-derived clinical observations are synthetic',validation_label:process.env.EPISCOPE_EVIDENCE_LABEL||'Scale not inferred from aggregate rows',release_sha256:crypto.createHash('sha256').update(bytes).digest('hex'),release_bytes:bytes.length,generated_at:release.generated_at,as_of:release.as_of,districts:release.districts.length,diseases:release.diseases.length,weeks:release.weeks.length,rows:Object.fromEntries(['observed','forecasts','signals','allocations','calendar','quality','tradeoff','scenarios'].map(k=>[k,release[k].length])),checks};
  fs.writeFileSync(path.join(evidence,'dashboard-browser-qa.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
  await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
