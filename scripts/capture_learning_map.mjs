#!/usr/bin/env node
// 静态架构图与完整开发工作台；业务截图单独获取，绝不生成假登录数据。
import {spawn} from 'node:child_process';
import {readFile,mkdir,mkdtemp,rm,writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {resolve,join,extname,dirname,sep} from 'node:path';
import {homedir,tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const wrapper=process.env.LEARNBUDDY_PWCLI || join(homedir(),'.codex/skills/playwright/scripts/playwright_cli.sh');
const output=join(root,'output/playwright/learning-map');
const assets=join(root,'docs/assets/learning-map');
const session='learning-map-'+process.pid;
const scratch=await mkdtemp(join(tmpdir(),'learnbuddy-map-'));
await mkdir(output,{recursive:true});await mkdir(assets,{recursive:true});
function run(args){return new Promise((ok,fail)=>{const p=spawn(wrapper,['-s='+session,...args],{cwd:scratch,stdio:['ignore','pipe','pipe']});let s='';p.stdout.on('data',c=>s+=c);p.stderr.on('data',c=>s+=c);p.on('error',fail);p.on('exit',code=>code?fail(new Error(s)):ok(s));});}
const types={'.html':'text/html; charset=utf-8','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml','.png':'image/png','.md':'text/plain; charset=utf-8'};
const server=createServer(async(req,res)=>{try{
const u=decodeURIComponent(new URL(req.url,'http://localhost').pathname);const path=resolve(root,'.'+u);
if(!path.startsWith(root+sep)||!(/^\/(docs|web)\//.test(u))||!types[extname(path)])throw Error('not allowed');
res.writeHead(200,{'Content-Type':types[extname(path)]});res.end(await readFile(path));
}catch{res.writeHead(404);res.end('Not found');}});
await new Promise(ok=>server.listen(0,'127.0.0.1',ok));const base='http://127.0.0.1:'+server.address().port;
const report={mode:'editable architecture figures and full existing development workbench; no business API',viewports:[],screenshots:[]};
try {
await run(['open',base+'/docs/LearnBuddy项目工作台.html']);
await run(['resize','1440','1100']);await run(['eval','() => document.fonts.ready']);await run(['snapshot']);
await run(['screenshot','--filename',join(assets,'workbench-overview.png')]);report.screenshots.push('workbench-overview.png');
await run(['screenshot','--full-page','--filename',join(assets,'workbench-full.png')]);report.screenshots.push('workbench-full.png');
await run(['eval',"() => { document.querySelector('header').style.position = 'static'; }"]);
await run(['screenshot','#iteration-v053','--filename',join(assets,'workbench-tasks.png')]);report.screenshots.push('workbench-tasks.png');
await run(['goto',base+'/docs/学习地图.html']);await run(['snapshot']);
for(const width of [1200,390]){
await run(['resize',String(width),'844']);
report.viewports.push(await run(['eval',`async () => {await document.fonts.ready;const imgs=[...document.images];await Promise.all(imgs.map(i=>i.decode()));if(document.documentElement.scrollWidth>innerWidth+1)throw Error('overflow');if(imgs.some(i=>!i.naturalWidth))throw Error('broken image');return {width:innerWidth,images:imgs.length,overflow:false};}`]));
await run(['screenshot','--filename',join(output,'map-'+width+'.png')]);
}
const figures=[['product','product-architecture.png'],['tokens','ai-design-contract.png'],['architecture','system-architecture.png'],['agent','agent-architecture.png']];
// 从 HTML 原始排版以 2x 像素密度直接渲染，不是放大已有位图。
await run(['run-code',`async (page) => {const context=await page.context().browser().newContext({viewport:{width:1200,height:1100},deviceScaleFactor:2});try{const p=await context.newPage();await p.goto(${JSON.stringify(base+'/docs/学习地图.html')});await p.evaluate(()=>document.fonts.ready);for(const [id,name] of ${JSON.stringify(figures)})await p.locator('#diagram-'+id).screenshot({path:${JSON.stringify(assets)}+'/'+name});}finally{await context.close();}}`]);
report.screenshots.push(...figures.map(x=>x[1]));
const errors=await run(['console','error']);if(!/Errors:\s*0\b/.test(errors))throw Error(errors);report.console=errors;
await writeFile(join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
}finally{await run(['close']).catch(()=>{});await new Promise(ok=>server.close(ok));await rm(scratch,{recursive:true,force:true});}
