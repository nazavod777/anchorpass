'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { parseProxy, parseAnchor, parseReload, parseAnchorToken, extractAction } = require('../dist/core.js');
const { verifyBundle } = require('../dist/updates.js');
const { anchor, signing } = require('./helpers.cjs');

test('shared proxy vectors and exact-once credentials', () => {
  const vectors = JSON.parse(fs.readFileSync(path.join(__dirname, 'proxy-vectors.json')));
  for (const vector of vectors) {
    if (vector.valid) assert.doesNotThrow(() => parseProxy(vector.value), String(vector.value));
    else assert.throws(() => parseProxy(vector.value), { code: 'InvalidProxy' }, String(vector.value));
  }
  assert.equal(parseProxy('http://a%2Bb:p%2540ss@localhost:80').username, 'a+b');
  assert.equal(parseProxy('http://a%2Bb:p%2540ss@localhost:80').password, 'p%40ss');
  assert.throws(() => parseProxy('http://user:%0A@localhost:80'), { code: 'InvalidProxy' });
});
test('strict anchor validation, canonical host, action precedence', () => {
  assert.equal(parseAnchor(anchor, 'explicit').query.get('sa'), 'explicit');
  assert.equal(parseAnchor(anchor).action, 'url-action');
  for(const action of ['', '界'.repeat(86), '\ud800']) assert.throws(()=>parseAnchor(anchor,action),{code:'InvalidInput'});
  const emptyAction=anchor.replace('sa=url-action','sa=');
  assert.throws(()=>parseAnchor(emptyAction),{code:'InvalidInput'});
  assert.equal(parseAnchor(emptyAction,'explicit').action,'explicit');
  assert.equal(extractAction(emptyAction),'');
  assert.equal(parseAnchor(anchor,'\0').action,'\0');
  assert.throws(()=>parseAnchor(anchor+'\0'),{code:'InvalidInput'});
  for (const input of [anchor + '&k=two', anchor + '#', anchor.replace('www.google.com', 'user@www.google.com'), anchor.replace('https:', 'http:'), anchor.replace('co=dGVzdA','co='), anchor + '&x=%FF', anchor.replace('/anchor', '/anchor/../anchor')]) {
    assert.throws(() => parseAnchor(input), { code: 'InvalidInput' }, input);
  }
  assert.equal(parseReload(")]}'\n[\"rresp\",\"token\"]"), 'token');
  assert.throws(() => parseReload('["rresp",""]'), { code: 'ProtocolError' });
});
test('signed bundle: exact bytes, signature, whitelist, time and ambiguous JSON', () => {
  const { keys, bundle, sample } = signing();
  assert.equal(verifyBundle(bundle(), keys).revision, '2');
  const tampered = JSON.parse(bundle()); tampered.signature = 'A'.repeat(86) + '==';
  assert.throws(() => verifyBundle(JSON.stringify(tampered), keys), { code: 'ConfigInvalid' });
  for (const patch of [{ revision:'02' }, { protocol_family:'other' }, { fields:{...sample.fields, extra:'x'} }, { issued_at:Math.floor(Date.now()/1000)+1000 }, { expires_at:1 }, { extra:'x' }]) {
    assert.throws(() => verifyBundle(bundle(patch), keys), { code:'ConfigInvalid' });
  }
  const raw = JSON.stringify({ ...sample, revision:'2' }).replace('"revision":"2"', '"revision":"2","revision":"3"');
  assert.throws(() => verifyBundle(bundle({}, raw), keys), { code:'ConfigInvalid' });
  const outer = bundle().replace('"key_id":"fixture"', '"key_id":"fixture","key_id":"fixture"');
  assert.throws(() => verifyBundle(outer, keys), { code:'ConfigInvalid' });
});

test('anchor attributes decode HTML5 entities with quoted attribute semantics',()=>{
  assert.equal(parseAnchorToken('<input id="recaptcha-token" value="anchor&amp;token">'),'anchor&token');
  assert.equal(parseAnchorToken('<INPUT VALUE="a&#38;&#x26;&quot;&apos;&copy;" ID="recaptcha-token">'), 'a&&"\'©');
  assert.equal(parseAnchorToken(`<input value='a"b>c&amp;d' id='recaptcha-token'>`),'a"b>c&d');
  assert.equal(parseAnchorToken('<input id="recaptcha-token" value="&amp;quot;">'),'&quot;');
  assert.equal(parseAnchorToken('<input id="recaptcha-token" value="&notit;">'),'&notit;');
});
test('anchor tokenizer accepts unquoted attributes and skips non-element markup',()=>{
  assert.equal(parseAnchorToken('<input disabled id=recaptcha-token value=anchor&amp;token>'),'anchor&token');
  const actual = '<INPUT VALUE=actual-token ID=recaptcha-token>';
  const decoy = '<input id="recaptcha-token" value="decoy">';
  for (const prefix of [
    `<!-- ${decoy} -->`, `<!-- ${decoy} --!>`,
    `<script>const example='${decoy}';</ScRiPt>`, `<style>/* ${decoy} */</style>`,
    `<textarea>${decoy}</textarea>`, `<title>${decoy}</title>`,
    `<div data-example='${decoy}'>`, '<?example ' + decoy + ' ?>',
    '<input-other id=recaptcha-token value=decoy>',
  ]) assert.equal(parseAnchorToken(prefix + actual),'actual-token',prefix);
  for (const source of [`<!-- ${decoy}`, `<script>${decoy}`, `<style>${decoy}`]) {
    assert.throws(()=>parseAnchorToken(source),{code:'ProtocolError'});
  }
});
test('canonical signed field and revision boundaries',()=>{
  const {keys,bundle,sample}=signing();
  for(const revision of ['0','01','1'.repeat(21)])assert.throws(()=>verifyBundle(bundle({revision}),keys),{code:'ConfigInvalid'});
  assert.equal(verifyBundle(bundle({revision:'9'.repeat(20)}),keys).revision,'9'.repeat(20));
  for(const field of Object.keys(sample.fields)) {
    assert.equal(verifyBundle(bundle({fields:{...sample.fields,[field]:'x'.repeat(5000)}}),keys).fields[field].length,5000);
    assert.equal(verifyBundle(bundle({fields:{...sample.fields,[field]:'x'.repeat(16384)}}),keys).fields[field].length,16384);
    assert.throws(()=>verifyBundle(bundle({fields:{...sample.fields,[field]:'x'.repeat(16385)}}),keys),{code:'ConfigInvalid'});
  }
  assert.equal(verifyBundle(bundle({fields:{...sample.fields,bg:'\0'}}),keys).fields.bg,'\0');
});
