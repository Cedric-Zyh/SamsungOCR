from pathlib import Path
import shutil
import subprocess

import pytest


def test_import_waits_for_explicit_start_without_remote_confirmation():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    completed = subprocess.run([node, '--test', 'tests/import_staging.cjs'],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_recursive_folder_collection_keeps_all_batches_and_paths():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    script = r'''
const assert = require('node:assert/strict');
const imp = require('./static/import_files.js');
const file = name => ({name, type: ''});
const entry = name => ({name, isFile:true, file:resolve=>resolve(file(name))});
const directory = (name, pages) => ({name, isDirectory:true, createReader:()=>{let i=0; return {readEntries:resolve=>resolve(pages[i++]||[])};}});
(async () => {
  const broken = {name:'unreadable.jpg',isFile:true,file:(resolve,reject)=>reject(Error('读取失败'))};
  const root = directory('回单', [
    Array.from({length:100},(_,i)=>entry(`${7000+i}.jpg`)),
    [directory('客户甲',[[entry('same.JPG'),directory('九月',[[entry('nested.png'),entry('notes.txt')]])]]),
     directory('客户乙',[[entry('same.JPG')]]), broken],
  ]);
  const transfer = {items:[{kind:'file',webkitGetAsEntry:()=>root,getAsFile:()=>null}],files:[]};
  const found = await imp.collectDrop(transfer);
  const selected = imp.summarize(found.files);
  assert.equal(found.files.length,104);
  assert.equal(selected.images.length,103);
  assert.equal(selected.skipped,1);
  assert.equal(found.errors[0].path,'回单/unreadable.jpg');
  assert.equal(new Set(selected.images.map(imp.relativePath)).size,103);
  assert(selected.images.some(f=>imp.relativePath(f)==='回单/客户甲/九月/nested.png'));
  assert(selected.images.some(f=>imp.relativePath(f)==='回单/客户乙/same.JPG'));
  assert.equal(imp.relativePath({name:'7.jpg',webkitRelativePath:'folder/a/b/7.jpg'}),'folder/a/b/7.jpg');
  assert.equal(imp.isImage(file('7.HEIC')),false);
  const plain=file('flat.webp');
  assert.equal((await imp.collectDrop({items:[],files:[plain]})).files[0],plain);
  assert.equal((await imp.collectDrop({items:[{kind:'file',getAsFile:()=>plain}],files:[plain]})).files.length,1);
  assert.equal((await imp.collectDrop({items:[{kind:'file',getAsFile:()=>null}],files:[]})).errors.length,1);
  const many = Array.from({length:5001},(_,i)=>i);
  assert.deepEqual(imp.batches(many).map(g=>g.length),[5000,1]);
  assert.deepEqual(imp.batches(many).flat(),many);
})().catch(error=>{console.error(error);process.exit(1)});
'''
    result = subprocess.run([node, '-e', script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
