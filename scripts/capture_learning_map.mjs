#!/usr/bin/env node
// 静态文档与登录页截图；不启动业务后端，不读取环境密钥或学习数据。
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
const report={mode:'static documentation and unsubmitted login page; no business API',viewports:[],screenshots:[]};
try {
await run(['open',base+'/web/login.html']);
await run(['resize','1062','820']);await run(['eval','() => document.fonts.ready']);
await run(['screenshot','--filename',join(assets,'login.png')]);report.screenshots.push('login.png');
await run(['goto',base+'/docs/LearnBuddy项目工作台.html']);
await run(['resize','390','844']);await run(['snapshot']);
// 仅解除粘性定位，避免元素截图时顶部导航覆盖目标区域。
await run(['eval',"() => { document.querySelector('header').style.position = 'static'; }"]);
await run(['screenshot','#open-learning','--filename',join(assets,'workbench.png')]);report.screenshots.push('workbench.png');
await run(['goto',base+'/docs/学习地图.html']);await run(['snapshot']);
for(const width of [1200,390]){
await run(['resize',String(width),'844']);
report.viewports.push(await run(['eval',`async () => {await document.fonts.ready;const imgs=[...document.images];await Promise.all(imgs.map(i=>i.decode()));if(document.documentElement.scrollWidth>innerWidth+1)throw Error('overflow');if(imgs.some(i=>!i.naturalWidth))throw Error('broken image');return {width:innerWidth,images:imgs.length,overflow:false};}`]));
await run(['screenshot','--filename',join(output,'map-'+width+'.png')]);
}
await run(['resize','1200','1100']);
for(const [id,name] of [['product','01-product-loop.png'],['tokens','02-design-tokens.png'],['architecture','04-system-architecture.png'],['agent','05-agent-state.png']]){
await run(['screenshot','#diagram-'+id,'--filename',join(assets,name)]);report.screenshots.push(name);
}
const errors=await run(['console','error']);if(!/Errors:\s*0\b/.test(errors))throw Error(errors);report.console=errors;
await writeFile(join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
}finally{await run(['close']).catch(()=>{});await new Promise(ok=>server.close(ok));await rm(scratch,{recursive:true,force:true});}
