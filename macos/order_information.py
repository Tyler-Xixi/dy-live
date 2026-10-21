"""Optional information entry inside the already verified product detail only."""
SELECTOR = 'input:not([type]),input[type="text" i],input[type="tel" i],input[type="email" i],input[type="url" i],input[type="search" i],textarea,[contenteditable="true"]'


class InformationError(Exception):
    pass


def ensure_information(scope,value,*,verify_only=False):
    if not value:
        return False
    try:
        candidates=[field for field in scope.locator(SELECTOR).all() if field.is_visible() and field.is_editable()]
        if len(candidates)!=1:
            raise InformationError('目标支付区的可填写信息框不是唯一一个，未提交')
        field=candidates[0]
        editable=field.get_attribute('contenteditable')=='true'
        current=field.inner_text() if editable else field.input_value()
        changed=current!=value
        if changed:
            if verify_only:
                raise InformationError('提交前下单填写信息变化，未提交')
            field.fill(value,timeout=700)
            field.blur(timeout=700)
        current=field.inner_text() if editable else field.input_value()
        if current!=value or not field.evaluate('(el)=>!el.validity||el.validity.valid'):
            raise InformationError('下单填写信息未通过页面核对，未提交')
        return changed
    except InformationError:
        raise
    except Exception:
        # Playwright error messages can include the fill value; do not expose it.
        raise InformationError('下单填写信息失败，未提交；请核对保留页面') from None


def redacted_html(page):
    return page.evaluate('''selector=>{const copy=document.documentElement.cloneNode(true);
      for(const el of copy.querySelectorAll(selector)){
        el.setAttribute('value','[已隐藏]');
        if(el.tagName==='TEXTAREA'||el.getAttribute('contenteditable')==='true')el.textContent='[已隐藏]';
      }return '<!DOCTYPE html>\\n'+copy.outerHTML;}''',SELECTOR)
