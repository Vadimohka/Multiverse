import base64
from typing import Any

import httpx
from bs4 import BeautifulSoup
from workflow_engine.egress import BrowserEgressGuard, EgressPolicy, request_with_egress_policy
from workflow_engine.transport import FetchPolicy


def stable_selector(tag: str, attrs: dict[str, Any], classes: list[str]) -> tuple[str, str]:
    if attrs.get("id"): return f"#{attrs['id']}", "HIGH"
    for key in ("data-testid", "data-id", "itemprop", "name"):
        if attrs.get(key): return f'{tag}[{key}="{attrs[key]}"]', "HIGH"
    filtered=[c for c in classes if not any(ch.isdigit() for ch in c) and len(c)<50]
    if filtered: return tag+"."+".".join(filtered[:2]), "MEDIUM"
    return tag, "LOW"


async def build_selector_snapshot(url: str, timeout: float = 30) -> dict[str, Any]:
    egress_policy = EgressPolicy()
    egress_policy.validate_url(url)
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            browser_context=await browser.new_context(viewport={"width":1440,"height":900})
            guard=BrowserEgressGuard(egress_policy)
            await guard.install(browser_context)
            page=await browser_context.new_page()
            await page.goto(url,wait_until="networkidle",timeout=int(timeout*1000))
            egress_policy.validate_url(page.url)
            guard.assert_safe()
            screenshot=await page.screenshot(full_page=True,type="png")
            screenshot_size=await page.evaluate("({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight})")
            elements=await page.locator("body *").evaluate_all("""els => els.slice(0,5000).map((el,i)=>{const r=el.getBoundingClientRect();return {index:i,tag:el.tagName.toLowerCase(),id:el.id||null,classes:[...el.classList],attributes:Object.fromEntries([...el.attributes].map(a=>[a.name,a.value])),text:(el.innerText||'').trim().slice(0,200),box:{x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:r.height}}}).filter(x=>x.box.width>0&&x.box.height>0)""")
            redirect_chain=guard.redirect_chain
            await browser_context.close()
            await browser.close()
        for el in elements:
            selector,score=stable_selector(el["tag"],el["attributes"],el["classes"]);el["css_selector_candidate"]=selector;el["stability_score"]=score
        return {"mode":"PLAYWRIGHT","url":page.url,"screenshot_base64":base64.b64encode(screenshot).decode(),"screenshot_size":screenshot_size,"elements":elements,"redirect_chain":redirect_chain}
    except Exception as browser_error:
        async with httpx.AsyncClient(follow_redirects=False,timeout=timeout) as client: response=await request_with_egress_policy(client,"GET",url,FetchPolicy(timeout=timeout,retries=0))
        soup=BeautifulSoup(response.text,"lxml"); elements=[]
        for i,el in enumerate(soup.select("body *")[:5000]):
            attrs=dict(el.attrs); classes=attrs.get("class",[]); classes=classes if isinstance(classes,list) else str(classes).split(); selector,score=stable_selector(el.name,attrs,classes)
            elements.append({"index":i,"tag":el.name,"id":attrs.get("id"),"classes":classes,"attributes":attrs,"text":el.get_text(" ",strip=True)[:200],"box":None,"css_selector_candidate":selector,"stability_score":score})
        return {"mode":"STATIC_HTML","url":str(response.url),"screenshot_base64":None,"elements":elements,"redirect_chain":response.extensions.get("redirect_chain", []),"warning":f"Playwright недоступен, использован статический DOM: {browser_error}"}
