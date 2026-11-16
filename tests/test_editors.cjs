const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const vendor = path.join(root, 'app/static/vendor/codemirror');
const cm = require(path.join(vendor, 'addon/runmode/runmode.node.js'));
for (const mode of ['jinja2', 'javascript', 'yaml', 'xml']) require(path.join(vendor, `mode/${mode}/${mode}.js`));
function styles(text, mode) {
  const tokens = [];
  cm.runMode(text, mode, (value, style) => {if (style) tokens.push({value, style});});
  return tokens;
}
assert(styles('{% for port in ports %}{{ port.name }}{% endfor %}', 'jinja2').some(t => t.style.includes('keyword')));
const json = styles('{"name":"router","mtu":1500,"enabled":true}', {name:'javascript',json:true});
for (const style of ['property', 'string', 'number', 'atom']) assert(json.some(t => t.style.includes(style)), style);
assert(styles('hostname: router\nmtu: 1500', 'yaml').length > 0);
for (const file of ['app.js','editors.js','filter-info.js']) new vm.Script(fs.readFileSync(path.join(root,'app/static',file),'utf8'));
// Verify the adapter keeps the existing form events and values in sync.
const fields = Object.fromEntries(['input','template','result','notice'].map(id => [id,{id,value:'',placeholder:'',events:[],dispatchEvent(event){this.events.push(event.type);}}]));
const editors = {};
const context = {
  document: {getElementById: id => fields[id], querySelector: () => ({addEventListener(){}})},
  window: {}, Event: class {constructor(type){this.type=type;}}, requestAnimationFrame: callback => callback(),
  CodeMirror: {fromTextArea(field, options) {
    const handlers = {};
    const editor = {value:'',options,historyCleared:0,
      getInputField(){return {setAttribute(){}};},
      on(name,handler){handlers[name]=handler;}, getValue(){return this.value;},
      setValue(value){this.value=value; handlers.change?.(this,{origin:'setValue'});},
      save(){field.value=this.value;}, getOption(name){return this.options[name];},
      setOption(name,value){this.options[name]=value;},clearHistory(){this.historyCleared++;},refresh(){},focus(){},
      userEdit(value){this.value=value;handlers.change?.(this,{origin:'+input'});}
    }; editors[field.id]=editor; return editor;
  }}
};
vm.runInNewContext(fs.readFileSync(path.join(root,'app/static/editors.js'),'utf8'),context);
const adapter=context.window.NetworkBenchEditors;
adapter.setValue('input','hostname: r1',true);
assert.equal(fields.input.value,'hostname: r1');
assert.equal(fields.input.events.length,0); // Loading must not cause duplicate saves.
assert.equal(editors.input.options.mode,'yaml');
editors.input.userEdit('{"hostname":"r2"}');
assert.equal(fields.input.value,'{"hostname":"r2"}');
assert.equal(fields.input.events.at(-1),'input'); // Existing live-render/autosave handler fires.
assert.equal(editors.input.options.mode.json,true);
adapter.setTool('jinja');assert.equal(editors.template.options.mode,'jinja2');
adapter.setTool('json');assert.equal(editors.input.options.mode.json,true);
assert.equal(editors.result.options.mode.json,true);
adapter.setValue('result','{"valid":true}');assert.equal(fields.result.value,'{"valid":true}');
adapter.setTool('textfsm');assert.equal(editors.input.options.mode,'text/plain');
adapter.setValue('input','');assert.equal(fields.input.value,'');
assert.equal(editors.result.options.readOnly,true);
const keys = editors.input.options.extraKeys;
let inserted, indentation;
const keyEditor = {
  somethingSelected: () => false,
  replaceSelection: (...args) => { inserted = args; },
  indentSelection: direction => { indentation = direction; }
};
keys.Tab(keyEditor);
assert.deepEqual(inserted, ['  ', 'end', '+input']);
keyEditor.somethingSelected = () => true;
keys.Tab(keyEditor); assert.equal(indentation, 'add');
keys['Shift-Tab'](keyEditor); assert.equal(indentation, 'subtract');
assert.equal(editors.input.options.indentUnit, 2);
assert.equal(typeof editors.template.options.extraKeys.Tab, 'function');
assert.equal(editors.result.options.extraKeys.Tab, false);
assert.equal(editors.result.options.extraKeys['Shift-Tab'], false);
console.log('PASS: language highlighting, JS syntax, mode switching, form sync, edit events, clear, and read-only output.');

adapter.setTool('json_yaml','json_to_yaml');
assert.equal(editors.input.options.mode.json,true);
assert.equal(editors.result.options.mode,'yaml');
adapter.setTool('json_yaml','yaml_to_json');
assert.equal(editors.input.options.mode,'yaml');
assert.equal(editors.result.options.mode.json,true);
console.log('PASS: JSON/YAML conversion editor modes.');
