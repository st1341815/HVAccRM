def lin(c):
    c/=255
    return c/12.92 if c<=0.03928 else ((c+0.055)/1.055)**2.4
def L(h):
    h=h.lstrip('#')
    if len(h)==3: h=''.join(x*2 for x in h)
    r,g,b=(int(h[i:i+2],16) for i in (0,2,4))
    return 0.2126*lin(r)+0.7152*lin(g)+0.0722*lin(b)
def cr(a,b):
    la,lb=L(a),L(b)
    if la<lb: la,lb=lb,la
    return (la+0.05)/(lb+0.05)

T={
'baseline':dict(bg='#efebe3',card='#fbf9f5',p2='#f7f4ee',p3='#f1efe9',ink='#45423c',muted='#6f6b64',line='#e5dfd5',cb='#e2dbd0',brand='#6c7f91',bs='#5b6f83',bi='#4f6376',b50='#e9edf1',b200='#b9c7d4',danger='#8f4a4a',dangerbg='#f7eded',succ='#4f7059',succbg='#e6ece1',warn='#7d6540',warnbg='#f4eedf',sbg='#3a3f47',sfg='#d6d2ca',smut='#a8adb4',rh='#f6f3ed'),
'A':dict(bg='#f4f6f7',card='#ffffff',p2='#f3f7f8',p3='#edf2f4',ink='#1c2529',muted='#5b6b71',line='#dfe6e9',cb='#d5dee2',brand='#0f6b73',bs='#0a5a61',bi='#0b5c64',b50='#e6f2f3',b200='#a8ccd0',danger='#a3272b',dangerbg='#fbeeed',succ='#1f6a41',succbg='#e8f3ea',warn='#7a5a13',warnbg='#fbf3dd',sbg='#1e2a2e',sfg='#d3dcde',smut='#93a3a7',rh='#e8f0f2'),
'B':dict(bg='#f5f7fa',card='#ffffff',p2='#f3f7fa',p3='#edf3fa',ink='#16202e',muted='#5c6a80',line='#e3e9f2',cb='#dbe3ee',brand='#2563eb',bs='#1d4ed8',bi='#1a4bc4',b50='#eaf1fe',b200='#b6cdf8',danger='#b42318',dangerbg='#fdeceb',succ='#146b3a',succbg='#e7f5ec',warn='#8a5300',warnbg='#fdf4e0',sbg='#16233a',sfg='#dbe4f2',smut='#94a4bd',rh='#ecf2fb'),
'C':dict(bg='#f7f2e8',card='#ffffff',p2='#f9f4e7',p3='#f1e9d9',ink='#332c22',muted='#6b6152',line='#e7dbc9',cb='#ddcfb7',brand='#a8562f',bs='#9c4a26',bi='#8a3f1e',b50='#f7e9e0',b200='#e0b79c',danger='#9c1f33',dangerbg='#fbeaee',succ='#2f6b3f',succbg='#e9f0e2',warn='#7a5200',warnbg='#f9efd6',sbg='#2e2820',sfg='#e2d8c9',smut='#a89c8a',rh='#f5ebd8'),
}
checks=[('正文/卡片','ink','card',4.5),('正文/页面底','ink','bg',4.5),('正文/斑马纹','ink','p2',4.5),
('次要字/卡片','muted','card',4.5),('次要字/斑马纹','muted','p2',4.5),('次要字/浅填充','muted','p3',4.5),
('白字/实心按钮','#ffffff','bs',4.5),('白字/主色(勿做按钮)','#ffffff','brand',4.5),('链接/卡片','bi','card',4.5),
('主色字/幽灵底','bi','b50',4.5),('侧栏字','sfg','sbg',4.5),('侧栏次要字','smut','sbg',4.5),
('危险字/危险底','danger','dangerbg',4.5),('成功字/成功底','succ','succbg',4.5),('警告字/警告底','warn','warnbg',4.5),
('发丝线/卡片(装饰)','line','card',1.3),('卡片边/页面底(装饰)','cb','bg',1.2),('悬浮行/卡片','rh','card',1.0)]
for name,t in T.items():
    print(f"\n===== {name} =====")
    bad=[]
    for lbl,f,b,need in checks:
        v=cr(t.get(f,f),t[b])
        ok='OK ' if v>=need else 'FAIL'
        if v<need: bad.append(lbl)
        print(f"  {ok} {lbl:<20} {v:5.2f} (需 {need})")
    print("  >> 不达标:", ", ".join(bad) if bad else "无")
