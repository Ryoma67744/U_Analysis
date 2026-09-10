// ★ ver66.3: 実ブラウザとは分けて、Promise/afterplot/世代入替の順序契約を検証する。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const frames = [], observers = [], errors = [], updates = [];
let held = null, holdNext = false;
const roots = new Map();
function graph() {
    return {isConnected: true, handlers: [], _fullLayout: {},
        data: [{marker:{size:2}, x:[1,2], y:[3,4], text:['a','b'], meta:'cluster'},
               {marker:{size:4}, x:[5], y:[6]}, {marker:{size:10}}, {marker:{size:7}}],
        layout: {meta:{kind:'umap',umap_style:{markers:[
            {index:0,delta:-1,role:'background'}, {index:1,delta:1,role:'highlight'},
            {index:2,delta:null,role:'legend'}, {index:3,delta:null,role:'draft'}]}},
            annotations:[{name:'umap_cluster_label',x:9,y:-8,font:{size:12}},
                         {name:'other',x:0,y:0,font:{size:9}}],
            xaxis:{range:[-1,10]}, yaxis:{range:[-2,11]}},
        on(event, fn) { this.handlers.push(fn); },
        emit() { this.handlers.forEach(fn=>fn()); }};
}
function root(gd) {
    return {isConnected:true, querySelectorAll(){return gd ? [gd] : [];}, matches(){return false;}};
}
const normal = graph(), fullscreen = graph();
roots.set('#interactive_umap_plot',root(normal));
roots.set('#umap_per_sample_container',root(null));
roots.set('#fs_umap_graph_container',root(fullscreen));
const nu = {};
const window = {dash_clientside:{no_update:nu}, requestAnimationFrame(fn){frames.push(fn);},
    Plotly:{
        restyle(gd, update, indices) {
            updates.push({kind:'marker',indices:[...indices]});
            indices.forEach((index,i)=>{gd.data[index].marker.size=update['marker.size'][i];});
            gd.emit();
            if (holdNext) { holdNext=false; return new Promise(resolve=>{held=resolve;}); }
            return Promise.resolve();
        },
        relayout(gd, update) {
            updates.push({kind:'label',keys:Object.keys(update)});
            Object.entries(update).forEach(([key,value])=>{
                const i=Number(key.match(/annotations\[(\d+)\]/)[1]);
                gd.layout.annotations[i].font.size=value;
            });
            gd.emit(); return Promise.resolve();
        }
    }};
const context=vm.createContext({window,document:{querySelector(s){return roots.get(s);}},
    MutationObserver: class {constructor(fn){this.fn=fn;observers.push(this);} observe(){} disconnect(){}},
    console:{error(...args){errors.push(args);}}, Promise,Number,WeakMap,Map,Array});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../../app/assets/umap_restyle.js'),'utf8'),context);
async function drain() {
    for(let i=0;i<40;i++) {
        const now=frames.splice(0); now.forEach(fn=>fn());
        for(let j=0;j<8;j++) await Promise.resolve();
        if(!frames.length && !held) return;
    }
    if(!held) assert.fail('更新が停止しない');
}
function sizes(gd){return gd.data.map(t=>t.marker.size);}
(async()=>{
    const values=JSON.stringify(normal.data.map(({marker,...rest})=>rest));
    window.dash_clientside.umap_restyle.normal(8,18);
    await drain();
    assert.deepEqual(sizes(normal),[7,9,10,7]);
    assert.deepEqual(sizes(fullscreen),[2,4,10,7]);
    assert.equal(normal.layout.annotations[0].font.size,18);
    assert.deepEqual([normal.layout.annotations[0].x,normal.layout.annotations[0].y],[9,-8]);
    assert.equal(JSON.stringify(normal.data.map(({marker,...rest})=>rest)),values);
    assert.equal(normal.layout.annotations[1].font.size,9);
    const count=updates.length; await drain(); assert.equal(updates.length,count);
    window.dash_clientside.umap_restyle.normal(1,18); await drain();
    assert.deepEqual(sizes(normal),[1,2,10,7]);
    // 同じDOMへ古い図が届いてもafterplotで最新設定を再適用する。
    const fresh=graph(); normal.data=fresh.data; normal.layout=fresh.layout; normal.emit();
    await drain(); assert.deepEqual(sizes(normal),[1,2,10,7]);
    assert.equal(normal.layout.annotations[0].font.size,18);
    // restyle待機中に図の世代と注釈順が変わる。旧添字で別注記を上書きしない。
    holdNext=true;
    window.dash_clientside.umap_restyle.normal(6,22); await drain();
    const newer=graph(); newer.layout.annotations.reverse();
    normal.data=newer.data; normal.layout=newer.layout; normal.emit();
    const release=held; held=null; release(); await drain();
    assert.deepEqual(sizes(normal),[5,7,10,7]);
    assert.equal(normal.layout.annotations[0].name,'other');
    assert.equal(normal.layout.annotations[0].font.size,9);
    assert.equal(normal.layout.annotations[1].font.size,22);
    // 全画面の設定は通常図を更新しない。
    window.dash_clientside.umap_restyle.fullscreen(10,24); await drain();
    assert.deepEqual(sizes(fullscreen),[9,11,10,7]);
    assert.deepEqual(sizes(normal),[5,7,10,7]);
    // 空divが先にmountされ、設定変更後にPlotlyがclassを付ける初期化順序。
    const mounted=graph();
    roots.set('#umap_per_sample_container',root(null));
    window.dash_clientside.umap_restyle.normal(12,26); await drain();
    roots.get('#umap_per_sample_container').querySelectorAll=()=>[mounted];
    mounted.matches=()=>true;
    observers.forEach(observer=>observer.fn([{type:'attributes',target:mounted}]));
    await drain();
    assert.deepEqual(sizes(mounted),[11,13,10,7]);
    assert.equal(mounted.layout.annotations[0].font.size,26);
    assert.equal(errors.length,0);
    // 文字サイズのrelayoutは保存APIへ渡さず、座標移動は従来どおり通す。
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../../app/assets/relayout_filter.js'),'utf8'),context);
    const filter=window.dash_clientside.relayout.filter_annotations;
    assert.equal(filter({'annotations[0].font.size':24}),nu);
    assert.equal(filter({'xaxis.range[0]':1}),nu);
    assert.notEqual(filter({'annotations[0].x':1}),nu);
    assert.notEqual(filter({'annotations[2].y':1}),nu);
    const mixed={'annotations[0].font.size':24,'annotations[2].x':3};
    assert.equal(filter(mixed).relayout,mixed);
    // 配列全体や削除は従来サーバも座標を抽出しない。保存対象には追加しない。
    assert.equal(filter({annotations:[{x:1,y:2}]}),nu);
    assert.equal(filter({'annotations[0]':null}),nu);
    console.log(JSON.stringify({passed:true,plotlyOperations:updates.length,checks:26}));
})().catch(error=>{console.error(error);process.exitCode=1;});
