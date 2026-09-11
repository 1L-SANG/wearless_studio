import test from 'node:test';
import assert from 'node:assert/strict';
import { loadEarningsHarness, findTree } from './helpers/mypageHarness.mjs';

const row = { id: 's1', paymentId: 'p1', createdAt: '2026-09-01T00:00:00Z', productName: '셔츠', sellerName: '상점', modelAmount: 10430 };
const props = { journey: { mode: 'active', flag: 'none' }, model: { id: 'm1', displayName: '모델', status: 'verified' }, license: { id: 'l1', status: 'active', vcId: 'proof-id', allowedUse: ['일반 의류'] } };
const button = (tree, label) => findTree(tree, node => node.type === 'button' && node.props.children === label);
const component = (tree, name) => findTree(tree, node => node.type?.name === name);
const textContent = node => node == null || typeof node === 'boolean' ? '' : Array.isArray(node) ? node.map(textContent).join(' ') : typeof node === 'object' ? textContent(node.props?.children) : String(node);
const settle = async (render, ready) => { let value; for (let i = 0; i < 20; i += 1) { await new Promise(resolve => setImmediate(resolve)); value = render(); if (ready(value)) return value; } return value; };

test('대화상자 내용이 바뀌면 제목으로 초점을 옮기고 마지막에는 지정된 열기 버튼으로 돌아가요', async () => {
  const harness = await loadEarningsHarness();
  const originalDocument = globalThis.document;
  const calls = [];
  const inferredOpener = { isConnected: true, focus: () => calls.push('inferred opener') };
  const returnOpener = { isConnected: true, focus: () => calls.push('return opener') };
  const dialogElement = { showModal: () => calls.push('show'), close: () => calls.push('close') };
  const headingElement = { focus: () => calls.push('heading') };
  try {
    globalThis.document = { activeElement: inferredOpener };
    const { MyPageDialog } = await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageDialog.jsx');
    const render = title => harness.render(MyPageDialog, {
      title,
      onClose: () => {},
      returnFocusRef: { current: returnOpener },
      children: null,
    });

    let tree = render('활동 관리');
    tree.props.ref.current = dialogElement;
    const heading = findTree(tree, node => node.type === 'h2');
    assert.ok(heading.props.ref, 'the heading exposes a focus target');
    heading.props.ref.current = headingElement;
    assert.equal(harness.runtime.effects.length, 2, 'mount and title changes have separate effects');
    const cleanup = harness.runtime.effects[0]();
    harness.runtime.effects[1]();

    tree = render('잠시 쉬어갈까요?');
    findTree(tree, node => node.type === 'h2').props.ref.current = headingElement;
    harness.runtime.effects[1]();
    cleanup();

    assert.deepEqual(calls, ['show', 'heading', 'heading', 'close', 'return opener']);
  } finally {
    globalThis.document = originalDocument;
    await harness.close();
  }
});

test('사용 상세에서 신고를 거쳐 돌아와도 원래 목록 링크를 복귀 초점으로 유지해요', async () => {
  const harness = await loadEarningsHarness({ reportUsage: async () => {} });
  try {
    const { MyPageUsage } = await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageUsage.jsx');
    const data = { rows: [row], loading: false, rowsError: false, markReported: () => {} };
    const render = () => harness.render(MyPageUsage, { data, month: '2026-09', onMonthChange: () => {} });
    const opener = { isConnected: true, focus() {} };

    let tree = render();
    findTree(tree, node => node.props?.['aria-haspopup'] === 'dialog').props.onClick({ currentTarget: opener });
    tree = render();
    let dialog = component(tree, 'MyPageDialog');
    assert.ok(dialog.props.returnFocusRef, 'the detail dialog receives the list opener');
    assert.equal(dialog.props.returnFocusRef.current, opener);

    button(tree, '사용 신고').props.onClick();
    tree = render();
    dialog = component(tree, 'MyPageDialog');
    assert.equal(dialog.props.title, '사용 신고');
    assert.equal(dialog.props.returnFocusRef.current, opener);

    dialog.props.onClose();
    tree = render();
    dialog = component(tree, 'MyPageDialog');
    assert.equal(dialog.props.title, '사용된 상세페이지');
    assert.equal(dialog.props.returnFocusRef.current, opener);
  } finally { await harness.close(); }
});

for (const conflict of [false, true]) test(`상세페이지 신고 ${conflict ? '중복 409' : '성공'} 후 신고됨으로 표시하고 중복 전송을 막아요`, async () => {
  const calls = [], updates = [];
  let resolveRequest;
  const harness = await loadEarningsHarness({ reportUsage: (id, reason) => {
    calls.push([id, reason]);
    return new Promise((resolve, reject) => { resolveRequest = () => conflict
      ? reject(Object.assign(new Error('already reported'), {status:409,code:'usage_already_reported'})) : resolve({}); });
  } });
  try {
    const {MyPageUsage} = await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageUsage.jsx');
    const data = {rows:[row],loading:false,rowsError:false,markReported:id=>{updates.push(id); data.rows=[{...row,reported:true}];}};
    const render = () => harness.render(MyPageUsage,{data,month:'2026-09',onMonthChange:()=>{}});
    let tree=render();
    findTree(tree,node=>node.props?.['aria-haspopup']==='dialog').props.onClick({currentTarget:{}});
    tree=render();
    assert.equal(component(tree,'MyPageDialog').props.title,'사용된 상세페이지');
    button(tree,'사용 신고').props.onClick();
    tree=render();
    findTree(tree,node=>node.type==='textarea').props.onChange({target:{value:'확인 요청'}});
    tree=render();
    const form=findTree(tree,node=>node.type==='form');
    const pending=form.props.onSubmit({preventDefault(){}});
    await form.props.onSubmit({preventDefault(){}});
    assert.deepEqual(calls,[['p1','확인 요청']]);
    tree=render();
    assert.equal(component(tree,'MyPageDialog').props.busy,true);
    assert.equal(findTree(tree,node=>node.type==='textarea').props.value,'확인 요청');
    resolveRequest(); await pending;
    tree=render();
    assert.deepEqual(updates,['p1']);
    assert.equal(component(tree,'MyPageDialog').props.title,'사용된 상세페이지');
    assert.ok(findTree(tree,node=>node.props?.role==='status' && node.props.children==='신고됨'));
    assert.equal(button(tree,'사용 신고'),null);
  } finally { await harness.close(); }
});

test('조건 편집은 마지막 품목을 유지하며 적용하기를 눌러야 저장해요', async () => {
  const calls=[], updates=[];
  const harness=await loadEarningsHarness({updateLicenseTerms:async(id,patch)=>{calls.push([id,patch]);return {id,...patch};}});
  try {
    const {MyPageConditions}=await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageConditions.jsx');
    const render=()=>harness.render(MyPageConditions,{...props,onLicenseChange:value=>updates.push(value)});
    let tree=render();
    findTree(tree,node=>node.type==='button' && Array.isArray(node.props.children) && node.props.children.includes('사용 조건 편집')).props.onClick();
    tree=render();
    findTree(tree,node=>node.type==='input' && node.props.checked).props.onChange();
    tree=render();
    assert.ok(findTree(tree,node=>node.props?.role==='alert' && node.props.children==='옷 종류는 최소 1개를 켜 두어야 해요.'));
    assert.ok(findTree(tree,node=>node.type==='input' && node.props.checked));
    findTree(tree,node=>node.type==='input' && !node.props.checked).props.onChange();
    assert.deepEqual(calls,[]);
    tree=render();
    await findTree(tree,node=>node.type==='form').props.onSubmit({preventDefault(){}});
    assert.deepEqual(calls,[['l1',{allowedUse:['일반 의류','액티브웨어']}]]);
    assert.equal(updates[0].status,'active');
    assert.deepEqual(updates[0].allowedUse,['일반 의류','액티브웨어']);
  } finally {await harness.close();}
});

test('운영팀 중지에서는 직접 재개할 수 없고 본인 중지만 재개 API에 연결해요',async()=>{
  const calls=[],updates=[];
  const harness=await loadEarningsHarness({resumeMyModel:async id=>{calls.push(id);return {status:'verified'};}});
  try{
    const {MyPageActivity}=await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageActivity.jsx');
    const input={...props,journey:{mode:'active',flag:'paused'},onDialogChange:()=>{},onModelChange:value=>updates.push(value)};
    let tree=harness.render(MyPageActivity,{...input,model:{...props.model,suspensionSource:'admin'},dialog:'manage'});
    assert.equal(button(tree,'활동 재개'),null);
    assert.ok(findTree(tree,node=>node.type==='a'&&node.props.href==='mailto:contact@wearless.kr'));
    assert.deepEqual(calls,[]);
    tree=harness.render(MyPageActivity,{...input,model:{...props.model,suspensionSource:'owner'},dialog:null});
    await button(tree,'활동 재개').props.onClick();
    assert.deepEqual(calls,['m1']); assert.equal(updates[0].status,'verified');
  }finally{await harness.close();}
});

for (const [hash,name] of [['#earnings','MyPageEarnings'],['#conditions','MyPageConditions']]) test(`${hash} 링크는 새 탭을 열고 키보드로 다른 탭에 이동해요`,async()=>{
  const harness=await loadEarningsHarness();
  try{
    const {ActiveDashboard}=await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageDashboard.jsx');
    harness.runtime.hash=hash;
    let tree=harness.render(ActiveDashboard,props);
    assert.ok(component(tree,name));
    const selected=findTree(tree,node=>node.props?.role==='tab'&&node.props['aria-selected']);
    selected.props.onKeyDown({key:'ArrowRight',preventDefault(){}});
    tree=harness.render(ActiveDashboard,props);
    assert.equal(harness.runtime.hash,hash==='#earnings'?'#license':'#usage');
    assert.equal(findTree(tree,node=>node.props?.role==='tab'&&node.props['aria-selected']).props.tabIndex,0);
  }finally{await harness.close();}
});

test('등록 진행은 정산 API를 호출하지 않고 정산 줄을 숨겨요',async()=>{
  let calls=0;
  const harness=await loadEarningsHarness({getSettlementSummary:async()=>{calls++;},listSettlements:async()=>{calls++;}});
  try{
    const {ActiveDashboard}=await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageDashboard.jsx');
    const tree=harness.render(ActiveDashboard,{...props,journey:{mode:'onboarding',step:2},enrollment:{status:'vc_pending'}});
    assert.ok(component(tree,'RegistrationProgress'));
    assert.equal(component(tree,'EarningsFigures'),null);
    for(const effect of harness.runtime.effects)effect();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(calls,0);
  }finally{await harness.close();}
});

test('입금 계좌 404는 미등록으로 열고 서버 마스킹 응답을 그대로 표시해요',async()=>{
  const calls=[];
  const fullAccount=['110','123','456','789'].join('');
  const harness=await loadEarningsHarness({getFacemarketConfig:async()=>({payoutBanks:[{code:'shinhan',name:'신한은행'}]}),http:async(path)=>{
    calls.push(path);
    if(calls.length===1)throw Object.assign(new Error('not found'),{status:404,code:'payout_account_not_found'});
    return {bankCode:'shinhan',bankName:'신한은행',holderName:'김서연',accountMasked:'***-****-6789'};
  }});
  try{
    const {BankSection,AccountShortcut,usePayoutAccount}=await harness.server.ssrLoadModule('/src/features/model/mypage/MyPagePayoutAccount.jsx');
    const accountApi=await harness.server.ssrLoadModule('/src/features/model/mypage/payoutAccount.js');
    const render=()=>harness.render(()=>usePayoutAccount('m1',true));
    render();
    for(const effect of harness.runtime.effects)effect();
    let bank=await settle(render,value=>value.phase!=='loading'); let tree=BankSection({bank,onOpen:()=>{}});
    assert.equal(calls[0],'/v1/facemarket/payout-account');
    assert.equal(button(tree,'계좌 등록').props.disabled,false);
    assert.match(String(findTree(tree,node=>node.type==='p').props.children),/본인 명의 계좌/);
    assert.deepEqual(bank.banks,[{code:'shinhan',name:'신한은행'}]);
    await accountApi.savePayoutAccount({bankCode:'shinhan',accountNumber:fullAccount,holderName:' 김서연 '});
    assert.deepEqual(calls.slice(0,2),['/v1/facemarket/payout-account','/v1/facemarket/payout-account']);
    bank.retry(); bank=render(); for(const effect of harness.runtime.effects)effect(); bank=await settle(render,value=>value.phase!=='loading');
    tree=BankSection({bank,onOpen:()=>{}});
    assert.match(String(findTree(tree,node=>node.type==='p').props.children),/신한은행 · \*\*\*-\*\*\*\*-6789 · 김서연/);
    assert.equal(AccountShortcut({bank,onOpen:()=>{}}).props.disabled,false);
  }finally{await harness.close();}
});

test('입금 계좌 503은 준비 중 문구를 유지하고 등록을 막아요',async()=>{
  const harness=await loadEarningsHarness({getFacemarketConfig:async()=>({}),http:async()=>{throw Object.assign(new Error('missing key'),{status:503,code:'payout_account_unconfigured'});}});
  try{
    const {BankSection,AccountShortcut,usePayoutAccount}=await harness.server.ssrLoadModule('/src/features/model/mypage/MyPagePayoutAccount.jsx');
    const render=()=>harness.render(()=>usePayoutAccount('m1',true)); render(); for(const effect of harness.runtime.effects)effect();
    const bank=await settle(render,value=>value.phase!=='loading'); const tree=BankSection({bank,onOpen:()=>assert.fail('must stay disabled')});
    assert.match(String(findTree(tree,node=>node.type==='p').props.children),/입금 계좌 등록은 준비 중이에요/);
    assert.equal(AccountShortcut({bank,onOpen:()=>{}}).props.disabled,true);
  }finally{await harness.close();}
});

test('다음 지급 문구와 월별 지급 상태를 정산 화면에 보여줘요',async()=>{
  const harness=await loadEarningsHarness({getSettlementSummary:async()=>({monthAmount:10430,monthCount:1,totalAmount:10430,totalCount:1}),listSettlements:async()=>[row],
    getPayoutStatements:async()=>({items:[{periodMonth:'2026-09',amount:10430,count:1,status:'scheduled',scheduledFor:'2026-10-10',paidAt:null,open:true}],nextPayout:{scheduledFor:'2026-10-10',amount:10430,periodMonth:'2026-09'}})});
  try{
    const {module,data}=await (async()=>{const data=await harness.load();return{module:harness.module,data};})();
    const earnings=module.MyPageEarnings({data,month:'2026-09',onMonthChange:()=>{}});
    assert.match(textContent(earnings),/2026년 9월/); assert.match(textContent(earnings),/예정/);
    const {NextPayout}=module; assert.match(textContent(NextPayout({nextPayout:data.statements.nextPayout})),/다음 지급 10월 10일 · 2026년 9월분 10,430원/);
  }finally{await harness.close();}
});

test('미리보기 실패와 이미지 오류는 자리표시로 돌아가고 상세 창을 열 때 URL을 새로 받아요',async()=>{
  let calls=0;
  const harness=await loadEarningsHarness({reportUsage:async()=>{},getPublicationPreviewUrl:async()=>{calls++;if(calls!==2)throw new Error('expired');return{url:'https://preview.test/fresh.png',expiresIn:600};}});
  try{
    const {MyPageUsage}=await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageUsage.jsx');
    const data={rows:[{...row,publicationId:'pub-1'}],loading:false,rowsError:false,markReported:()=>{}};
    const render=()=>harness.render(MyPageUsage,{data,month:'2026-09',onMonthChange:()=>{}});
    let tree=render(); for(const effect of harness.runtime.effects)effect(); await new Promise(resolve=>setImmediate(resolve)); tree=render();
    assert.equal(findTree(tree,node=>node.type==='img'),null);
    await findTree(tree,node=>node.props?.['aria-haspopup']==='dialog').props.onClick({currentTarget:{}}); await new Promise(resolve=>setImmediate(resolve)); tree=render();
    const image=findTree(tree,node=>node.type==='img'); assert.equal(image.props.src,'https://preview.test/fresh.png'); assert.equal(calls,2);
    image.props.onError(); tree=render(); assert.equal(findTree(tree,node=>node.type==='img'),null);
    await findTree(tree,node=>node.props?.['aria-haspopup']==='dialog').props.onClick({currentTarget:{}}); await new Promise(resolve=>setImmediate(resolve));
    assert.equal(findTree(render(),node=>node.type==='img'),null); assert.equal(calls,3);
  }finally{await harness.close();}
});

test('발급 전 증서 번호를 숨기고 발급일은 서울 날짜로 표시해요',async()=>{
  const harness=await loadEarningsHarness();
  try{
    const {MyPageCertificate,ProfileHero}=await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageCertificate.jsx');
    const pending=MyPageCertificate({license:{...props.license,status:'pending'},model:props.model});
    assert.ok(component(pending,'EmptyPanel'));
    assert.equal(findTree(pending,node=>node.type==='Link'),null);
    const license={...props.license,createdAt:'2026-09-02T17:00:00Z'};
    const hero=ProfileHero({license,model:props.model});
    assert.ok(findTree(hero,node=>node.props?.children==='모델 · 2026. 09. 03 발급'));
    const issued=MyPageCertificate({license,model:props.model});
    assert.equal(findTree(issued,node=>node.type==='Link').props.to,'/verify/l1');
  }finally{await harness.close();}
});
