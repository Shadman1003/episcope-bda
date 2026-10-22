/* Shared policy-plot checks. Requires an actual national dashboard session. */
const assert=require('assert/strict');
const fs=require('fs');
const path=require('path');

async function verifyNationalPolicyPlots(page,{screenshotDir}={}){
  const original=await page.evaluate(()=>window.epiScopeState);
  assert.equal(original.scope,'national');
  const panel=page.locator('#policy-checks');
  assert.equal(await panel.isVisible(),true,'National policy section is unavailable');
  const wasOpen=await panel.evaluate(node=>node.open);
  const controls={threshold:await page.locator('#threshold').inputValue(),budget:await page.locator('#budget').inputValue(),equity:await page.locator('#equity').inputValue(),resource:await page.locator('#resource').inputValue(),disease:await page.locator('.disease-tab').evaluateAll(nodes=>nodes.findIndex(node=>node.getAttribute('aria-pressed')==='true'))};
  if(!wasOpen)await page.locator('#policy-checks > summary').click();
  await page.waitForFunction(()=>document.querySelectorAll('#detection-policy .detection-point').length===14);
  const detection=page.locator('#detection-policy .detection-point'),scenarios=page.locator('#allocation-policy .allocation-policy-point');
  assert.equal(await detection.count(),14,'Detection grid lost evaluated settings');
  assert.equal(await scenarios.count(),24,'Allocation grid lost coincident scenarios');
  assert.equal(await page.locator('#allocation-policy .allocation-policy-curve').count(),6,'Expected six national budget lines');
  for(const id of ['detection-policy','allocation-policy'])assert.equal(await page.locator(`#${id} > svg`).getAttribute('role'),'group','Plot must expose its focusable point labels');
  const selectedDetection=page.locator('#detection-policy .detection-point.selected');
  const selectedScenario=page.locator('#allocation-policy .allocation-policy-point.selected');
  const snapshot=()=>page.locator('#detection-policy .detection-point, #allocation-policy .allocation-policy-point').evaluateAll(nodes=>nodes.map(node=>[node.getAttribute('data-method'),node.getAttribute('data-threshold'),node.getAttribute('data-budget'),node.getAttribute('data-equity'),node.getAttribute('transform')]));
  const allCoordinates=await snapshot();
  await page.locator('#district').selectOption('0');
  const diseaseCount=await page.locator('.disease-tab').count();
  await page.locator('.disease-tab').nth((controls.disease+1)%diseaseCount).click();
  const resourceCount=await page.locator('#resource option').count();
  if(resourceCount>1)await page.locator('#resource').selectOption(String((Number(controls.resource)+1)%resourceCount));
  assert.deepEqual(await snapshot(),allCoordinates,'National policy coordinates followed district/disease/resource filters');
  await page.locator('#heatmap rect.cell').nth(80).focus();await page.keyboard.press('Enter');
  assert.deepEqual(await snapshot(),allCoordinates,'National policy coordinates followed historical date selection');
  await page.locator('#reset').click();
  await page.locator('.disease-tab').nth(controls.disease).click();
  await page.locator('#resource').selectOption(controls.resource);
  await page.locator('#district').selectOption(original.district);
  await page.locator('#threshold').fill('4');await page.locator('#threshold').dispatchEvent('input');
  assert.equal(await selectedDetection.count(),1);
  assert.equal(await selectedDetection.getAttribute('data-method'),'EARS_C2');
  assert.equal(Number(await selectedDetection.getAttribute('data-threshold')),4,'Detector selection did not follow multiplier');
  assert.equal(await selectedDetection.locator('.evaluation-selection-ring').count(),1);
  await page.locator('#threshold').fill('4.5');await page.locator('#threshold').dispatchEvent('input');
  assert.equal(Number(await selectedDetection.getAttribute('data-threshold')),4,'Nearest evaluated multiplier tie must be deterministic');
  await page.locator('#budget').fill(await page.locator('#budget').getAttribute('max'));await page.locator('#budget').dispatchEvent('input');
  await page.locator('#equity').fill(await page.locator('#equity').getAttribute('max'));await page.locator('#equity').dispatchEvent('input');
  const policy=await page.evaluate(()=>window.epiScopeState);
  assert.equal(await selectedScenario.count(),1);
  assert.ok(Math.abs(Number(await selectedScenario.getAttribute('data-budget'))-policy.budget)<.005,'Budget did not update selected scenario');
  assert.ok(Math.abs(Number(await selectedScenario.getAttribute('data-equity'))-policy.equity)<.0001,'Equity did not update selected scenario');
  assert.equal(await selectedScenario.locator('.evaluation-selection-ring').count(),1);
  await detection.first().focus();await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(()=>document.activeElement?.matches('#detection-policy .detection-point')),true,'Tab did not reach a detection point');
  await page.waitForTimeout(500); // Let keyboard-triggered smooth scrolling finish.
  assert.equal(await page.locator('#tooltip').isVisible(),true,'Focused detection tooltip disappeared during scrolling');
  assert.match(await page.locator('#tooltip').innerText(),/Median issue delay \(detected events\)/);
  await page.keyboard.press('Escape');assert.equal(await page.locator('#tooltip').isVisible(),false);
  await page.mouse.wheel(0,80);await page.waitForTimeout(250);
  assert.equal(await page.locator('#tooltip').isVisible(),false,'Scrolling restored an Escape-dismissed tooltip');
  await scenarios.first().focus();await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(()=>document.activeElement?.matches('#allocation-policy .allocation-policy-point')),true,'Tab did not reach an allocation point');
  await page.waitForTimeout(500);
  assert.equal(await page.locator('#tooltip').isVisible(),true,'Focused allocation tooltip disappeared during scrolling');
  const allocationTip=await page.locator('#tooltip').innerText();assert.match(allocationTip,/Budget/);assert.match(allocationTip,/equity floor/);assert.match(allocationTip,/Weighted unmet need reduction/);
  await page.keyboard.press('Escape');
  for(const id of ['threshold','budget','equity']){await page.locator('#'+id).fill(controls[id]);await page.locator('#'+id).dispatchEvent('input');}
  assert.equal(Number(await selectedDetection.getAttribute('data-threshold')),original.threshold,'Default evaluated setting did not restore');
  assert.ok(Math.abs(Number(await selectedScenario.getAttribute('data-budget'))-original.budget)<.005);
  assert.ok(Math.abs(Number(await selectedScenario.getAttribute('data-equity'))-original.equity)<.0001);
  await page.locator('#reset').focus();
  if(screenshotDir){
    fs.mkdirSync(screenshotDir,{recursive:true});
    await panel.screenshot({path:path.join(screenshotDir,'dashboard-policy-checks-desktop.png')});
    await page.locator('#detection-policy').locator('..').screenshot({path:path.join(screenshotDir,'dashboard-policy-detection.png')});
    await page.locator('#allocation-policy').locator('..').screenshot({path:path.join(screenshotDir,'dashboard-policy-allocation.png')});
  }
  const viewport=page.viewportSize();await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false,'Expanded policy charts overflow mobile width');
  assert.equal(await detection.count(),14);assert.equal(await scenarios.count(),24);
  if(screenshotDir){
    // Fit the complete panel before an element capture. Chromium otherwise
    // composites off-screen fixed skip links into clips taller than viewport.
    const panelHeight=Math.ceil((await panel.boundingBox()).height);
    await page.setViewportSize({width:390,height:Math.max(844,panelHeight+40)});await page.waitForTimeout(200);
    await panel.screenshot({path:path.join(screenshotDir,'dashboard-policy-checks-mobile.png')});
  }
  await page.setViewportSize(viewport);await page.waitForTimeout(200);
  if(!wasOpen)await page.locator('#policy-checks > summary').click();
  assert.deepEqual(await page.evaluate(()=>window.epiScopeState),original,'Policy QA did not restore shared state');
  return ['national_policy_14_detection_24_scenarios_six_budgets','national_policy_scope_ignores_district_disease_resource_date','policy_control_selected_rings_and_nearest_threshold','policy_keyboard_tooltips_escape','expanded_policy_responsive_390px'];
}

async function verifyDistrictPolicyHidden(page,payload){
  assert.equal(payload.access.role,'district');
  assert.deepEqual(payload.tradeoff,[],'District response retained national detector evaluations');
  assert.deepEqual(payload.scenarios,[],'District response retained national scenario evaluations');
  assert.equal(await page.locator('#policy-checks').isVisible(),false,'District view displayed national policy section');
  assert.equal(await page.locator('#detection-policy svg, #allocation-policy svg').count(),0,'National plot SVG remained after scope change');
  assert.equal(await page.locator('#detection-policy .detection-point, #allocation-policy .allocation-policy-point').count(),0);
  assert.equal(await page.locator('#allocation-policy-legend > *').count(),0);
  for(const id of ['detection-policy-note','allocation-policy-note'])assert.equal(await page.locator('#'+id).textContent(),'','National selection caption survived district scope');
  return 'district_policy_payload_hidden_and_dom_cleared';
}

module.exports={verifyNationalPolicyPlots,verifyDistrictPolicyHidden};
