const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const nodes = new Map(), writes = [];
const records = [
  {id:'old',tool:'jinja',name:'Office router',updated_at:100,template:'old',input:'',options:{},revision:1},
  {id:'new',tool:'jinja',name:'Core router',updated_at:200,template:'new',input:'',options:{},revision:1},
  {id:'json',tool:'json',name:'JSON settings',updated_at:300,template:'',input:'{}',options:{},revision:1}
];
function node(key) {
  if (!nodes.has(key)) nodes.set(key, {value:'', textContent:'', handlers:{}, options:[], classList:{toggle(){}},
    addEventListener(e,f){this.handlers[e]=f},replaceChildren(...options){this.options=options},add(option){this.options.push(option)},setAttribute(){}});
  return nodes.get(key);
}
const context = vm.createContext({document:{querySelector:node,querySelectorAll:()=>[],body:{}},
  window:{addEventListener(){}},Option:function(text,value){this.text=text;this.value=value},AbortController,
  setTimeout:()=>1,clearTimeout(){},confirm(){throw Error('Unexpected confirmation')},
  NetworkBenchFilterInfo:{setVisible(){}},NetworkBenchEditors:{setValue(id,value){node('#'+id).value=value},setTool(){}},
  fetch:async(url,options)=>{
    if (options.method) {
      writes.push({url,...options});
      return {ok:true,status:201,json:async()=>({id:'draft',revision:2})};
    }
    const value = url.includes('examples') ? {} : url.includes('?') ? records : records.find(item=>url.endsWith('/'+item.id));
    return {ok:true,status:200,json:async()=>value};
  }
});
const run = code => vm.runInContext(code,context);
run(fs.readFileSync(path.join(__dirname,'../app/static/app.js'),'utf8'));
(async()=>{
  await new Promise(setImmediate);
  assert.equal(node('#saved-count').textContent,'2');
  assert.deepEqual(node('#saved').options.slice(1).map(o=>o.value),['new','old']);
  assert.match(node('#saved').options[1].text,/Core router — /);
  node('#saved-search').value='OFFICE';node('#saved-search').handlers.input();
  assert.deepEqual(node('#saved').options.slice(1).map(o=>o.value),['old']);
  await node('#open-latest').handlers.click();
  assert.equal(run('active.id'),'new');
  assert.equal(node('#template').value,'new');
  assert.equal(writes.length,0);
  node('#input').value='edited';node('#input').handlers.input();
  await run("openWorkspace('old')");
  assert.equal(writes.length,1);
  assert.equal(writes[0].method,'PUT');
  assert.equal(JSON.parse(writes[0].body).input,'edited');
  assert.equal(run('active.id'),'old');
  run("display(blank('json'))");
  assert.equal(node('#saved-search').value,'');
  assert.equal(node('#saved-count').textContent,'1');
  assert.equal(node('#saved').options[1].value,'json');
  run("display(blank('ttp'))");
  assert.equal(node('#open-latest').disabled,true);
  assert.equal(node('#saved').disabled,true);
  run("display(blank('jinja')); examples={jinja:[{name:'Example',template:'{{ x }}',data:'x: 1'}]}");
  node('#examples').value='0';node('#examples').handlers.change();
  await run('save(active)');
  assert.equal(writes.length,1);
  await run("openWorkspace('new')");
  assert.equal(writes.length,1);
  console.log('PASS: per-tool history, latest-first order, search, latest shortcut, loading, save-before-open, empty state, and unsaved examples.');
})().catch(error=>{console.error(error);process.exitCode=1});
