/* EpiScope dashboard. All required charts use the locally vendored D3 v7.
 * No encounter, patient, facility or hidden truth table is loaded here.
 */
"use strict";
const $ = (selector) => document.querySelector(selector);
const fmt = new Intl.NumberFormat("en", {maximumFractionDigits: 0});
const compact = new Intl.NumberFormat("en", {notation: "compact", maximumFractionDigits: 1});
const dateFormat = d3.utcFormat("%d %b %Y");
const shortDate = d3.utcFormat("%d %b");
const parseDate = (value) => new Date(value + "T00:00:00Z");
const day = 86400000;
const label = (name) => ({dengue:"Dengue",diarrhoea:"Diarrhoeal disease",diarrhea:"Diarrhoeal disease",influenza:"Influenza / ILI",malaria:"Malaria",iv_saline:"IV saline",ns1_kits:"NS1 kits",ors:"ORS",antimalarials:"Antimalarials",beds:"Beds",response_teams:"Response teams"}[name] || name);
const palette = {teal:"#138581",purple:"#7b6bad",orange:"#bc7b3a",stock:"#c4d1d8"};
let data, state = {disease:0,district:"all",range:null,threshold:3,budget:0,equity:.2,resource:0};
let budgets=[], equities=[], weeks=[], latestObservation, latestPlanning;
let renderTimer;
const tip = $("#tooltip");

function tooltip(event, title, lines=[]) {
  tip.replaceChildren();
  const strong = document.createElement("strong"); strong.textContent=title; tip.append(strong);
  for (const line of lines) { const div=document.createElement("div"); div.textContent=line; tip.append(div); }
  tip.hidden=false;
  const box=tip.getBoundingClientRect();
  tip.style.left=Math.max(8,Math.min(window.innerWidth-box.width-12,event.clientX+14))+"px";
  tip.style.top=Math.max(8,Math.min(window.innerHeight-box.height-12,event.clientY+12))+"px";
}
function hideTip(){tip.hidden=true;}
function dimensions(selector, fallbackHeight) {
  const element=$(selector), bounds=element.getBoundingClientRect();
  return {width:Math.max(240,bounds.width),height:bounds.height||fallbackHeight};
}
function svgFor(selector, height) {
  const {width}=dimensions(selector,height);
  d3.select(selector).selectAll("*").remove();
  const svg=d3.select(selector).append("svg").attr("viewBox",`0 0 ${width} ${height}`).attr("width","100%").attr("height",height).attr("role","img");
  return {svg,width,height};
}
function inRange(date){return +date>=+state.range[0]&&+date<=+state.range[1];}
function districtMatches(index){return state.district==="all"||index===Number(state.district);}
function selectedObserved(){return data.observed.filter(r=>r[1]===state.disease&&districtMatches(r[0])&&inRange(weeks[r[2]]));}
function selectedForecasts(){return data.forecasts.filter(r=>r[1]===state.disease&&districtMatches(r[0]));}
function currentSignals(){return data.signals.filter(r=>r[1]===state.disease&&districtMatches(r[0])&&inRange(parseDate(r[2])));}
function districtName(){return state.district==="all"?"All districts":data.districts[Number(state.district)].district_name;}
function selectedPopulation(){return d3.sum(data.districts.filter((_,i)=>districtMatches(i)),r=>r.population);}
function sumKnown(rows,position){const known=rows.filter(r=>r[position]!==null);return known.length?d3.sum(known,r=>r[position]):null;}
function changeDistrict(value){state.district=String(value);$("#district").value=String(value);render();}
function focusWeek(date){const start=d3.utcMonday.floor(date);state.range=[d3.utcWeek.offset(start,-4),d3.utcWeek.offset(start,4)];render();}
function selectedDiseaseResource(){const disease=data.diseases[state.disease];return disease==="dengue"?"ns1_kits":disease.startsWith("diarr")?"ors":disease==="malaria"?"antimalarials":"beds";}
function setDisease(index){state.disease=index;const resource=data.resources.indexOf(selectedDiseaseResource());if(resource>=0)state.resource=resource;$("#resource").value=state.resource;render();}
function scaleLegend(selector, scale, max, title){
  const root=d3.select(selector);root.selectAll("*").remove();
  root.append("span").text(title);
  const svg=root.append("svg").attr("width",150).attr("height",23).attr("viewBox","0 0 150 23");
  d3.range(60).forEach(i=>svg.append("rect").attr("x",i*2).attr("y",1).attr("width",2).attr("height",6).attr("fill",scale(max*i/59)));
  svg.append("text").attr("x",0).attr("y",20).text("0");svg.append("text").attr("x",120).attr("y",20).attr("text-anchor","end").text(d3.format(".2~s")(max));
  root.append("span").text("▧ Withheld / no release");
}

function renderKPIs(){
  const observed=selectedObserved(), forecasts=selectedForecasts();
  const known=observed.filter(r=>r[3]!==null), missing=observed.length-known.length;
  $("#kpi-cases").textContent=known.length?fmt.format(d3.sum(known,r=>r[3])):"—";
  $("#kpi-period").textContent=`${shortDate(state.range[0])} – ${dateFormat(state.range[1])} · ${missing?missing+" cells withheld":"five-count bins"}`;
  $("#kpi-alerts").textContent=fmt.format(new Set(currentSignals().filter(r=>r[6]!==null&&r[6]>=state.threshold&&r[8]).map(r=>r[0])).size);
  $("#kpi-threshold").textContent=currentSignals().length?`EARS-C2 ≥ ${state.threshold.toFixed(1)}σ · ${data.signal_as_of||data.as_of}`:"Latest signals lie outside selected dates";
  const quality=d3.mean(observed,r=>r[4]);$("#kpi-quality").textContent=quality===undefined?"—":d3.format(".0%")(quality);
  const predicted=sumKnown(forecasts,4);$("#kpi-forecast").textContent=predicted===null?"—":fmt.format(predicted);
  const lower=sumKnown(forecasts,5),upper=sumKnown(forecasts,6);
  const forecastMissing=forecasts.filter(r=>r[4]===null).length;
  $("#kpi-interval").textContent=lower===null?"Forecast withheld where cells are small":`${fmt.format(lower)}–${fmt.format(upper)} · ${forecastMissing?forecastMissing+" forecast cells withheld":state.district==="all"?"summed district bounds":"80% interval"}`;
  $("#selection-pill").textContent=districtName();
}

function incidenceRows(){
  const rows=data.observed.filter(r=>r[1]===state.disease&&inRange(weeks[r[2]]));
  const groups=d3.group(rows,r=>r[0]);
  return data.districts.map((district,index)=>{
    const group=groups.get(index)||[],known=group.filter(r=>r[3]!==null), cases=d3.sum(known,r=>r[3]);
    return {district,index,cases,incidence:known.length?cases/district.population*100000:null,withheld:group.length-known.length,quality:d3.mean(group,r=>r[4]),weeks:group.length};
  });
}

function renderMap(){
  const height=dimensions("#map",330).height,{svg,width}=svgFor("#map",height);
  svg.append("title").text("Bangladesh district incidence and daily alert markers. Select a district to update every view.");
  const rows=incidenceRows(),byId=new Map(rows.map(r=>[r.district.district_id,r]));
  const max=Math.max(1,d3.max(rows,r=>r.incidence)||1),color=d3.scaleSequential(d3.interpolateBuGn).domain([0,max]);
  const pattern=svg.append("defs").append("pattern").attr("id","suppressed-map").attr("width",5).attr("height",5).attr("patternUnits","userSpaceOnUse");
  pattern.append("rect").attr("width",5).attr("height",5).attr("fill","#e8edee");pattern.append("path").attr("d","M0,5L5,0").attr("stroke","#b4c3c9").attr("stroke-width",1);
  if(!data.geography.features.length){svg.append("text").attr("x",width/2).attr("y",height/2).attr("text-anchor","middle").text("District geography is not available in this release.");return;}
  const projection=d3.geoMercator().fitExtent([[25,10],[width-25,height-12]],data.geography),path=d3.geoPath(projection);
  svg.selectAll("path.district-shape").data(data.geography.features).join("path").attr("class",f=>"district-shape"+(String(byId.get(f.properties.district_id)?.index)===state.district?" selected":"")).attr("d",path).attr("fill",f=>{const r=byId.get(f.properties.district_id);return r?.incidence===null?"url(#suppressed-map)":color(r?.incidence||0);}).attr("opacity",f=>state.district==="all"||String(byId.get(f.properties.district_id)?.index)===state.district?1:.4).attr("tabindex",0).attr("role","button").attr("aria-label",f=>`Select ${byId.get(f.properties.district_id)?.district.district_name||"district"}`).on("click",(_,f)=>changeDistrict(byId.get(f.properties.district_id).index)).on("keydown",(event,f)=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();changeDistrict(byId.get(f.properties.district_id).index);}}).on("pointermove",(event,f)=>{
    const r=byId.get(f.properties.district_id);tooltip(event,r.district.district_name,[r.incidence===null?"Case cells withheld":`${d3.format(".1f")(r.incidence)} released cases / 100,000`,`${fmt.format(r.cases)} visible cases · ${r.withheld} withheld weeks`,`${d3.format(".0%")(r.quality||0)} mean reporting completeness`,"Select to coordinate all views"]);
  }).on("pointerleave",hideTip);
  const alerted=new Set(currentSignals().filter(r=>r[6]!==null&&r[6]>=state.threshold&&r[8]).map(r=>r[0]));
  svg.selectAll("circle.alert-marker").data(data.geography.features.filter(f=>alerted.has(byId.get(f.properties.district_id)?.index))).join("circle").attr("class","alert-marker").attr("cx",f=>path.centroid(f)[0]).attr("cy",f=>path.centroid(f)[1]).attr("r",4.5);
  scaleLegend("#map-legend",color,max,"Incidence");d3.select("#map-legend").append("span").text("● Daily review signal");
  $("#map-caption").textContent=`${shortDate(state.range[0])}–${dateFormat(state.range[1])} · visible cells only`;
}

function weeklySeries(){
  const observed=data.observed.filter(r=>r[1]===state.disease&&districtMatches(r[0]));
  const group=d3.group(observed,r=>r[2]);
  const result=weeks.map((date,index)=>{const rows=group.get(index)||[];return{date,value:sumKnown(rows,3),withheld:rows.filter(r=>r[3]===null).length,quality:d3.mean(rows,r=>r[4])};});
  result.forEach((point,index)=>{const history=result.slice(Math.max(0,index-8),index).filter(r=>r.value!==null).map(r=>r.value);point.threshold=history.length>=4?d3.mean(history)+state.threshold*(d3.deviation(history)||0):null;});
  const forecasts=Array.from(d3.group(selectedForecasts(),r=>r[2]),([date,rows])=>({date:parseDate(date),value:sumKnown(rows,4),lower:sumKnown(rows,5),upper:sumKnown(rows,6)})).sort((a,b)=>a.date-b.date);
  return {observed:result,forecasts};
}

function renderSeries(){
  const height=dimensions("#series",268).height,{svg,width}=svgFor("#series",height),margin={top:13,right:23,bottom:32,left:52};
  svg.append("title").text("Observed weekly cases, a weekly review threshold, and four-week forecasts with uncertainty. Use the date navigator to filter every view.");
  const series=weeklySeries(),x=d3.scaleUtc().domain(state.range).range([margin.left,width-margin.right]);
  const visible=[...series.observed.filter(r=>inRange(r.date)),...series.forecasts.filter(r=>inRange(r.date))];
  const max=Math.max(5,d3.max(visible,r=>Math.max(r.value||0,r.upper||0,r.threshold||0))||5),y=d3.scaleLinear().domain([0,max*1.12]).nice().range([height-margin.bottom,margin.top]);
  svg.append("g").attr("class","grid").attr("transform",`translate(${margin.left},0)`).call(d3.axisLeft(y).ticks(4).tickSize(-(width-margin.left-margin.right)).tickFormat(""));
  svg.append("g").attr("class","axis").attr("transform",`translate(0,${height-margin.bottom})`).call(d3.axisBottom(x).ticks(Math.max(3,width/120)).tickSizeOuter(0));
  svg.append("g").attr("class","axis").attr("transform",`translate(${margin.left},0)`).call(d3.axisLeft(y).ticks(4).tickFormat(d3.format("~s")).tickSizeOuter(0));
  svg.append("text").attr("x",15).attr("y",9).attr("font-size",8).text("Cases");
  svg.append("defs").append("clipPath").attr("id","series-clip").append("rect").attr("x",margin.left).attr("y",margin.top).attr("width",width-margin.left-margin.right).attr("height",height-margin.top-margin.bottom);
  const plot=svg.append("g").attr("clip-path","url(#series-clip)");
  const line=accessor=>d3.line().defined(r=>accessor(r)!==null).x(r=>x(r.date)).y(r=>y(accessor(r)));
  plot.append("path").datum(series.observed).attr("fill","none").attr("stroke",palette.teal).attr("stroke-width",2).attr("d",line(r=>r.value));
  plot.append("path").datum(series.observed).attr("fill","none").attr("stroke",palette.orange).attr("stroke-width",1.2).attr("stroke-dasharray","4 4").attr("d",line(r=>r.threshold));
  plot.append("path").datum(series.forecasts).attr("fill","#e5dff2").attr("fill-opacity",.85).attr("d",d3.area().defined(r=>r.lower!==null&&r.upper!==null).x(r=>x(r.date)).y0(r=>y(r.lower)).y1(r=>y(r.upper)));
  plot.append("path").datum(series.forecasts).attr("fill","none").attr("stroke",palette.purple).attr("stroke-width",2).attr("stroke-dasharray","5 3").attr("d",line(r=>r.value));
  plot.selectAll("circle.forecast-point").data(series.forecasts.filter(r=>r.value!==null)).join("circle").attr("cx",r=>x(r.date)).attr("cy",r=>y(r.value)).attr("r",2.5).attr("fill",palette.purple);
  const dates=visible.filter(r=>r.value!==null).sort((a,b)=>a.date-b.date),bisect=d3.bisector(r=>r.date).center;
  const hover=plot.append("line").attr("y1",margin.top).attr("y2",height-margin.bottom).attr("stroke","#9bb5b7").attr("stroke-dasharray","2 3").attr("visibility","hidden");
  svg.append("rect").attr("x",margin.left).attr("y",margin.top).attr("width",width-margin.left-margin.right).attr("height",height-margin.top-margin.bottom).attr("fill","transparent").on("pointermove",(event)=>{
    if(!dates.length)return;const point=dates[bisect(dates,x.invert(d3.pointer(event)[0]))];hover.attr("x1",x(point.date)).attr("x2",x(point.date)).attr("visibility","visible");
    const lines=[`${point.upper!==undefined?"Forecast":"Released observations"}: ${fmt.format(point.value)} cases`];if(point.upper!==undefined)lines.push(`80% district bounds: ${fmt.format(point.lower)}–${fmt.format(point.upper)}`);else{lines.push(`Withheld district cells: ${point.withheld}`);if(point.threshold!==null)lines.push(`Weekly review line: ${fmt.format(point.threshold)}`);}tooltip(event,dateFormat(point.date),lines);
  }).on("pointerleave",()=>{hideTip();hover.attr("visibility","hidden");}).call(d3.zoom().scaleExtent([1,20]).filter(event=>event.type==="wheel").on("zoom",event=>{
    if(!event.sourceEvent)return;const next=event.transform.rescaleX(x).domain();if(+next[1]-+next[0]>7*day){state.range=next;clearTimeout(renderTimer);renderTimer=setTimeout(render,70);}
  }));
  renderNavigator(series.observed);
  $("#series-caption").textContent=`${districtName()} · ${state.district==="all"?"ribbon sums district 80% bounds; national coverage uncalibrated":"80% prediction interval"}`;
}

function renderNavigator(observed){
  const height=48,{svg,width}=svgFor("#navigator",height),margin={left:52,right:23};
  const extent=[weeks[0],d3.utcWeek.offset(latestObservation,5)],x=d3.scaleUtc().domain(extent).range([margin.left,width-margin.right]);
  const y=d3.scaleLinear().domain([0,d3.max(observed,r=>r.value)||1]).range([29,3]);
  svg.append("path").datum(observed).attr("fill","#e4efed").attr("d",d3.area().defined(r=>r.value!==null).x(r=>x(r.date)).y0(29).y1(r=>y(r.value)));
  svg.append("g").attr("class","axis").attr("transform","translate(0,31)").call(d3.axisBottom(x).ticks(d3.utcYear.every(1)).tickFormat(d3.utcFormat("%Y")).tickSize(0));
  const brush=d3.brushX().extent([[margin.left,1],[width-margin.right,30]]).on("end",event=>{if(!event.sourceEvent||!event.selection)return;state.range=event.selection.map(x.invert);render();});
  svg.append("g").attr("class","brush").call(brush).call(brush.move,state.range.map(x));
}

function isoWeek(date){const thursday=new Date(+date);thursday.setUTCDate(thursday.getUTCDate()+3-(thursday.getUTCDay()+6)%7);const year=thursday.getUTCFullYear(),first=new Date(Date.UTC(year,0,4));return{year,week:1+Math.round(((thursday-first)/day-3+(first.getUTCDay()+6)%7)/7)};}

function renderHeatmap(){
  const height=dimensions("#heatmap",218).height,{svg,width}=svgFor("#heatmap",height),margin={left:47,right:20,top:17,bottom:35};
  svg.append("title").text("Seasonal weekly incidence by ISO year. Select a cell to focus the linked date range.");
  const population=selectedPopulation(),series=weeklySeries().observed.map(r=>({...r,...isoWeek(r.date),incidence:r.value===null?null:r.value/population*100000}));
  const years=Array.from(new Set(series.map(r=>r.year))).sort(),x=d3.scaleBand().domain(d3.range(1,54)).range([margin.left,width-margin.right]).padding(.12),y=d3.scaleBand().domain(years).range([margin.top,height-margin.bottom]).padding(.16);
  const max=Math.max(.1,d3.quantile(series.filter(r=>r.incidence!==null).map(r=>r.incidence).sort(d3.ascending),.95)||1),color=d3.scaleSequential(d3.interpolateBuGn).domain([0,max]).clamp(true);
  svg.selectAll("rect.cell").data(series).join("rect").attr("class","cell").attr("x",r=>x(r.week)).attr("y",r=>y(r.year)).attr("width",x.bandwidth()).attr("height",y.bandwidth()).attr("rx",1).attr("fill",r=>r.incidence===null?"#dfe5e8":color(r.incidence)).attr("stroke",r=>inRange(r.date)?"#174b55":"none").attr("stroke-width",r=>inRange(r.date)?.6:0).attr("opacity",r=>inRange(r.date)?1:.68).attr("tabindex",0).attr("role","button").attr("aria-label",r=>`Focus ISO week ${r.week}, ${r.year}`).on("click",(_,r)=>focusWeek(r.date)).on("keydown",(event,r)=>{if(event.key==="Enter"){focusWeek(r.date);}}).on("pointermove",(event,r)=>tooltip(event,`ISO ${r.year} · week ${r.week}`,[`${dateFormat(r.date)}`,r.incidence===null?"Withheld":`${d3.format(".2f")(r.incidence)} visible cases / 100,000`,`${r.withheld} district cells withheld`,"Select for an eight-week window"])).on("pointerleave",hideTip);
  svg.append("g").attr("class","axis").attr("transform",`translate(0,${height-margin.bottom+5})`).call(d3.axisBottom(x).tickValues([1,9,17,25,33,41,49]).tickSize(0).tickFormat(d=>d));
  svg.append("g").attr("class","axis").attr("transform",`translate(${margin.left-5},0)`).call(d3.axisLeft(y).tickSize(0));svg.selectAll(".axis path").remove();
  svg.append("text").attr("x",(width+margin.left)/2).attr("y",height-5).attr("text-anchor","middle").attr("font-size",8).text("ISO epidemic week");
  scaleLegend("#heatmap-legend",color,max,"Weekly incidence");
  const divisions=state.district==="all"?Array.from(new Set(data.districts.map(r=>r.division))):[data.districts[Number(state.district)].division];
  const period=isoWeek(state.range[1]),calendar=data.calendar.filter(r=>r[1]===state.disease&&divisions.includes(r[0])&&r[2]>=period.week&&r[2]<=Math.min(53,period.week+8));
  const peak=d3.greatest(calendar,r=>r[3]);
  $("#calendar-note").textContent=peak?`${peak[0]} · ${peak[5]} preparedness: ${peak[4]} (epi-week ${peak[2]}, seasonal index ${d3.format(".2f")(peak[3])}).`:"Preparedness calendar: select a district and a seasonal week to inspect its division’s planned action.";
}

function allocationRows(){return data.allocations.filter(r=>r[1]===state.resource&&r[2]===state.budget&&Math.abs(r[3]-state.equity)<.0001&&districtMatches(r[0]));}
function renderAllocation(){
  const height=dimensions("#allocation",249).height,{svg,width}=svgFor("#allocation",height),margin={left:89,right:25,top:12,bottom:30};
  svg.append("title").text("Latest planning cycle: resource demand, current stock, recommended additional allocation and population-proportportional baseline.");
  const all=allocationRows(),known=all.filter(r=>r[4]!==null),rows=known.sort((a,b)=>(b[4]-b[5])-(a[4]-a[5])).slice(0,state.district==="all"?7:1);
  if(!rows.length){svg.append("text").attr("x",width/2).attr("y",height/2).attr("text-anchor","middle").text(all.length?"Small balance measures withheld together.":"No allocation scenario is available.");}
  const x=d3.scaleLinear().domain([0,Math.max(5,d3.max(rows,r=>Math.max(r[4],r[5]+r[6],r[5]+r[8]))||5)]).nice().range([margin.left,width-margin.right]),y=d3.scaleBand().domain(rows.map(r=>r[0])).range([margin.top,height-margin.bottom]).padding(.4);
  svg.append("g").attr("class","grid").attr("transform",`translate(0,${height-margin.bottom})`).call(d3.axisBottom(x).ticks(4).tickSize(-(height-margin.top-margin.bottom)).tickFormat(""));
  svg.append("g").attr("class","axis").attr("transform",`translate(0,${height-margin.bottom})`).call(d3.axisBottom(x).ticks(4).tickFormat(d3.format("~s")).tickSizeOuter(0));
  svg.append("g").attr("class","axis").attr("transform",`translate(${margin.left-8},0)`).call(d3.axisLeft(y).tickFormat(d=>data.districts[d].district_name).tickSize(0)).select("path").remove();
  const bars=svg.selectAll("g.bar").data(rows).join("g").attr("class","bar").attr("transform",r=>`translate(0,${y(r[0])})`).attr("tabindex",0).attr("role","button").attr("aria-label",r=>`Select ${data.districts[r[0]].district_name} allocation`).on("click",(_,r)=>changeDistrict(r[0])).on("keydown",(event,r)=>{if(event.key==="Enter")changeDistrict(r[0]);}).on("pointermove",(event,r)=>tooltip(event,data.districts[r[0]].district_name,[`Upper-bound demand: ${fmt.format(r[4])}`,`Current stock: ${fmt.format(r[5])}`,`Recommended top-up: ${fmt.format(r[6])}`,`Remaining unmet: ${fmt.format(r[7])}`,`Population baseline: ${fmt.format(r[8])}`,"All quantities are safe five-count display bins."])).on("pointerleave",hideTip);
  const band=Math.min(16,y.bandwidth());bars.append("rect").attr("x",x(0)).attr("y",0).attr("width",r=>Math.max(0,x(r[5])-x(0))).attr("height",band).attr("rx",1).attr("fill",palette.stock);
  bars.append("rect").attr("x",r=>x(r[5])).attr("y",0).attr("width",r=>Math.max(0,x(r[5]+r[6])-x(r[5]))).attr("height",band).attr("fill",palette.teal);
  bars.append("line").attr("x1",r=>x(r[4])).attr("x2",r=>x(r[4])).attr("y1",-4).attr("y2",band+4).attr("stroke",palette.orange).attr("stroke-width",2);
  bars.append("rect").attr("x",r=>x(r[5]+r[8])-2.5).attr("y",band/2-2.5).attr("width",5).attr("height",5).attr("fill","white").attr("stroke",palette.purple).attr("stroke-width",1.4);
  const historic=+state.range[1]<+latestObservation;$("#allocation").classList.toggle("muted-plan",historic);
  const summary=data.scenarios.find(r=>r.budget===state.budget&&Math.abs(r.equity_floor-state.equity)<.0001);
  $("#scenario-note").textContent=historic?"Historical time selection. This chart remains the latest planning cycle, not a reconstruction of historical stocks.":summary?`Scenario comparison: ${summary.benefit_vs_baseline_pct===null?"unavailable":d3.format("+.1f")(summary.benefit_vs_baseline_pct)+"%"} improvement versus population allocation; transport utilisation ${summary.transport_utilisation===null?"unavailable":d3.format(".0%")(summary.transport_utilisation)}. ${all.length-known.length} small balance tuples withheld.`:`${rows.length} visible district plans · ${all.length-known.length} balance tuples withheld · all stock and forecasts use the latest cycle.`;
  $("#allocation-caption").textContent=`${label(data.resources[state.resource]||"Resource")} · ${data.forecast_origin||data.as_of} planning origin · ${rows.length<known.length?"top seven by net need":"selected scope"}`;
}

function renderWarnings(){
  const periods=d3.group(selectedObserved(),r=>r[0]),signals=new Map(currentSignals().map(r=>[r[0],r]));
  const rows=data.districts.map((district,index)=>({district,index,signal:signals.get(index),period:periods.get(index)||[]})).filter(r=>districtMatches(r.index)).sort((a,b)=>(b.signal?.[6]??-Infinity)-(a.signal?.[6]??-Infinity));
  const body=d3.select("#warning-body");body.selectAll("*").remove();
  rows.forEach(row=>{
    const signal=row.signal,valid=signal&&signal[6]!==null,complete=signal?.[8],alert=valid&&complete&&signal[6]>=state.threshold,missing=row.period.some(r=>r[3]===null),quality=d3.mean(row.period,r=>r[4]);
    const tr=body.append("tr").attr("data-district",row.index).attr("tabindex",0).attr("aria-label",`Select ${row.district.district_name}`).on("click",()=>changeDistrict(row.index)).on("keydown",event=>{if(event.key==="Enter")changeDistrict(row.index);});
    tr.append("td").text(row.district.district_name);
    tr.append("td").append("span").attr("class","badge "+(alert?"alert":!valid||!complete?"missing":"quiet")).text(alert?"Review signal":!signal?"Outside dates":signal[9]?"Backfill pause":!valid?"Withheld":!complete?"Incomplete":"Monitor");
    const score=tr.append("td");score.append("span").text(valid?d3.format(".2f")(signal[6]):"—");if(valid)score.append("span").attr("class","score-bar").append("i").style("width",Math.min(100,Math.max(0,signal[6])*10)+"%");
    const known=sumKnown(row.period,3);tr.append("td").text(known===null?"Withheld":fmt.format(known)+(missing?" + withheld":""));
    tr.append("td").text(quality===undefined?"—":d3.format(".0%")(quality));
    tr.append("td").text(signal?.[9]?"Suspected reporting backfill: alert temporarily withheld":!complete&&signal?"Verify reporting completeness":alert?"Verify reports; review local readiness":!valid?"Use released weekly pattern; verify source":"Continue routine surveillance");
  });
  const metric=data.tradeoff.filter(r=>r.method==="EARS_C2").sort((a,b)=>Math.abs(a.threshold_multiplier-state.threshold)-Math.abs(b.threshold_multiplier-state.threshold))[0];
  $("#warning-caption").textContent=metric?`Nearest evaluated multiplier ${metric.threshold_multiplier}: sensitivity ${metric.sensitivity===null?"unavailable":d3.format(".0%")(metric.sensitivity)}, false alerts ${metric.false_alerts_per_district_year===null?"unavailable":d3.format(".2f")(metric.false_alerts_per_district_year)} / district-year. Signals require verification.`:"Signals support review; they are not outbreak diagnoses. Small daily numerators or baselines suppress the score.";
}

function render(){
  if(!data)return;hideTip();
  d3.selectAll(".disease-tab").classed("active",(_,i)=>i===state.disease).attr("aria-pressed",(_,i)=>String(i===state.disease));
  $("#budget-value").textContent=compact.format(state.budget)+" units";$("#equity-value").textContent=d3.format(".0%")(state.equity);$("#threshold-value").textContent=state.threshold.toFixed(1)+"σ";
  renderKPIs();renderMap();renderSeries();renderHeatmap();renderAllocation();renderWarnings();
  // A read-only diagnostic hook helps automated integration checks inspect the
  // actual linked UI state without exposing any additional source data.
  window.epiScopeState=Object.freeze({district:state.district,disease:data.diseases[state.disease],range:state.range.map(d=>d.toISOString().slice(0,10)),threshold:state.threshold,budget:state.budget,equity:state.equity,scope:data.access.role});
}

function renderQuality(){
  const rows=data.quality||[];$("#quality-panel").hidden=!rows.length;
  const body=d3.select("#quality-body");body.selectAll("*").remove();
  rows.forEach(row=>{
    const tr=body.append("tr");tr.append("td").text((row.batch_id||"").slice(0,12));
    tr.append("td").text((row.loaded_at||"").replace("T"," ").slice(0,19));
    ["rows_in","rows_out","quarantined","duplicates"].forEach(key=>tr.append("td").text(row[key]===null?"Withheld":fmt.format(row[key])));
    const detail=tr.append("td").append("details");detail.append("summary").text("View checks");
    const list=detail.append("ul");(row.rules||[]).forEach(rule=>list.append("li").text(rule.rule.replaceAll("_"," ")+": "+(rule.approximate_pass_rate===null?"rate withheld":d3.format(".0%")(rule.approximate_pass_rate)+" pass (approx.)")));
  });
}

function init(){
  weeks=data.weeks.map(parseDate);latestObservation=weeks[weeks.length-1];latestPlanning=parseDate(data.forecast_origin||data.as_of);
  state.range=[d3.utcWeek.offset(latestObservation,-25),d3.utcWeek.offset(latestObservation,5)];
  budgets=Array.from(new Set(data.allocations.map(r=>r[2]))).sort((a,b)=>a-b);equities=Array.from(new Set(data.allocations.map(r=>r[3]))).sort((a,b)=>a-b);
  state.budget=budgets[Math.floor(budgets.length/2)]||0;state.equity=equities.includes(.2)?.2:equities[0]||0;
  const tabs=d3.select("#disease-tabs");tabs.selectAll("*").remove();tabs.selectAll("button").data(data.diseases).join("button").attr("type","button").attr("class","disease-tab").text(label).on("click",(_,name)=>setDisease(data.diseases.indexOf(name)));
  const districts=d3.select("#district");districts.selectAll("*").remove();if(data.access.role==="national")districts.append("option").attr("value","all").text("All districts");
  districts.selectAll("option.district-option").data(data.districts).join("option").attr("class","district-option").attr("value",(_,i)=>i).text(r=>r.district_name);state.district=data.access.role==="district"?"0":"all";districts.property("value",state.district).on("change",event=>changeDistrict(event.target.value));
  $("#clear-district").hidden=data.access.role!=="national";$("#clear-district").onclick=()=>changeDistrict("all");
  const resources=d3.select("#resource");resources.selectAll("*").remove();resources.selectAll("option").data(data.resources).join("option").attr("value",(_,i)=>i).text(label);state.resource=Math.max(0,data.resources.indexOf(selectedDiseaseResource()));resources.property("value",state.resource).on("change",event=>{state.resource=+event.target.value;render();});
  $("#budget").max=Math.max(0,budgets.length-1);$("#budget").value=budgets.indexOf(state.budget);$("#budget").disabled=!budgets.length;$("#budget").oninput=event=>{state.budget=budgets[+event.target.value]||0;render();};
  $("#equity").max=Math.max(0,equities.length-1);$("#equity").value=equities.indexOf(state.equity);$("#equity").disabled=!equities.length;$("#equity").oninput=event=>{state.equity=equities[+event.target.value]||0;render();};
  $("#threshold").value=state.threshold;$("#threshold").oninput=event=>{state.threshold=+event.target.value;render();};
  $("#reset").onclick=()=>{state.range=[d3.utcWeek.offset(latestObservation,-25),d3.utcWeek.offset(latestObservation,5)];state.district=data.access.role==="district"?"0":"all";$("#district").value=state.district;render();};
  $("#access-label").textContent=data.access.label;$(".workspace-label").textContent=`BANGLADESH · ${data.districts.length} ${data.districts.length===1?"DISTRICT":"DISTRICTS"}`;$("h1").textContent=data.access.role==="district"?data.districts[0].district_name+" surveillance overview":"National surveillance overview";$("#release-date").textContent="Data through · "+dateFormat(parseDate(data.as_of));
  const rawSize=new TextEncoder().encode(JSON.stringify(data)).length;$("#release-size").textContent=`${(rawSize/1000000).toFixed(2)} MB · aggregate data only`;
  const generated=new Date(data.generated_at);d3.selectAll(".freshness").text("Updated "+d3.utcFormat("%d %b · %H:%M UTC")(generated));
  $("#notice").className="notice";$("#notice").textContent=`${data.label||"SYNTHETIC HEALTH DATA"} · ${data.districts.length} districts in this access scope · weekly release · daily signals · four-week planning`;
  $("#privacy-note").textContent=data.privacy.rounding+" "+data.privacy.policy+" "+data.privacy.limitations;
  $("#evaluation-note").textContent="A lower signal multiplier increases sensitivity and alert workload. The warning footer shows the nearest actually evaluated operating point; no performance improvement is assumed.";
  renderQuality();render();
}

async function load(){
  try{
    const token=sessionStorage.getItem("episcope-token"),response=await fetch("/api/data",{headers:token?{Authorization:"Bearer "+token}:{},cache:"no-store"});
    const payload=await response.json();if(!response.ok)throw new Error(payload.error||"Unable to load release");data=payload;init();
  }catch(error){$("#notice").className="notice error";$("#notice").textContent=error.message+" Start the local serving server after running the gold export. If authentication is enabled, use Access token.";}
}
$("#access-button").onclick=()=>$("#token-dialog").showModal();
$("#token-submit").onclick=()=>{const token=$("#token-input").value.trim();if(token)sessionStorage.setItem("episcope-token",token);else sessionStorage.removeItem("episcope-token");$("#token-input").value="";load();};
window.addEventListener("resize",()=>{clearTimeout(renderTimer);renderTimer=setTimeout(render,150);});
document.addEventListener("scroll",hideTip,{passive:true});
load();
