"""Home Assistant's authenticated pairing panel."""

import html
from aiohttp import web

SUPERVISOR = "172.30.32.2"


def panel(fleet):
    @web.middleware
    async def ingress_only(request, handler):
        peer = (
            request.transport.get_extra_info("peername") if request.transport else None
        )
        if not peer or peer[0] != SUPERVISOR:
            raise web.HTTPForbidden()
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def rows():
        result = []
        for rt in fleet.runtimes.values():
            tv = fleet.info(rt)
            control = (
                "invalid"
                if getattr(rt.engine.problem, "code", None) == "pairing_removed"
                else "paired"
                if tv["paired"]
                else "missing"
            )
            screen = (
                "invalid"
                if getattr(rt.observer, "invalid", False) is True
                else "paired"
                if tv["screen_paired"]
                else "missing"
            )
            result.append(
                dict(
                    tv=tv["id"],
                    name=tv["name"],
                    host=tv["host"],
                    control=control,
                    screen=screen,
                    pending=rt.pairing.prompt(),
                )
            )
        return result

    async def index(request):
        cards = []
        for row in rows():
            escape = lambda v: html.escape(str(v or ""))
            cards.append(f"""<section data-tv="{escape(row["tv"])}"><h2>{escape(row["name"])}</h2>
<p>{escape(row["host"])}</p><p class="states">Control: {escape(row["control"])} · Screen: {escape(row["screen"])}</p>
<button data-kind="control">Pair control</button> <button data-kind="screen">Pair screen</button>
<form hidden><label class="ask"></label> <input name="code" aria-label="Pairing code" inputmode="numeric" autocomplete="one-time-code" required>
<button>Submit code</button> <button type="button" class="cancel">Cancel</button></form><output aria-live="polite"></output></section>""")
        return web.Response(
            content_type="text/html",
            text="""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Apple TV Bridge</title>
<style>body{font:1rem system-ui;color-scheme:light dark;max-width:42rem;margin:2rem auto;padding:0 1rem}
section{border-top:1px solid gray;padding:1rem 0}button,input{font:inherit;padding:.45rem}form,output{margin-top:1rem}output{display:block}</style>
<h1>Apple TV Bridge</h1>"""
            + "".join(cards)
            + """<script>
const rooms=[...document.querySelectorAll('section')];
function render(room,pending){
  const form=room.querySelector('form');
  if(room.prompt?.pairing_id!==pending?.pairing_id)form.reset();
  room.prompt=pending; form.hidden=!pending;
  for(const button of room.querySelectorAll('[data-kind]'))button.disabled=!!pending;
  if(pending){form.querySelector('.ask').textContent=pending.ask_user;
    form.elements.code.disabled=form.querySelector('button:not([type])').disabled=pending.state==='waiting_for_pairing_screen';
    form.elements.code.pattern='[0-9]{'+pending.code_length+'}';form.elements.code.maxLength=pending.code_length;}
}
async function refresh(){
  const response=await fetch('status');
  if(!response.ok)throw Error('Pairing status unavailable');
  for(const row of await response.json()){
    const room=rooms.find(r=>r.dataset.tv===row.tv);
    if(room&&!room.busy){room.querySelector('.states').textContent='Control: '+row.control+' · Screen: '+row.screen;
      render(room,row.pending);}
  }
}
async function action(room,args){
  if(room.busy)return;
  room.busy=true;
  const output=room.querySelector('output');
  try{const response=await fetch('pair',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({...args,tv:room.dataset.tv})});
    if(!response.ok)throw Error('Could not contact Apple TV Bridge');
    const result=await response.json();output.textContent=result.ok?result.summary:result.summary+' '+result.error.fix;
  }catch(e){output.textContent=e.message;}finally{room.busy=false;await refresh();}
}
for(const room of rooms){
  for(const btn of room.querySelectorAll('[data-kind]'))btn.onclick=()=>action(room,{kind:btn.dataset.kind});
  const form=room.querySelector('form');
  form.onsubmit=e=>{e.preventDefault();action(room,{kind:room.prompt.kind,pairing_id:room.prompt.pairing_id,code:form.elements.code.value});};
  form.querySelector('.cancel').onclick=()=>action(room,{kind:room.prompt.kind,pairing_id:room.prompt.pairing_id,cancel:true});
}
refresh();setInterval(refresh,4000);</script></html>""",
        )

    async def status(request):
        return web.json_response(rows())

    async def pair(request):
        # JSON-only mutation prevents cross-site HTML form submissions.
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        args = await request.json()
        if not isinstance(args, dict) or not args.get("tv"):
            raise web.HTTPBadRequest()
        return web.json_response(await fleet.call("tv_pair", args))

    app = web.Application(middlewares=[ingress_only], client_max_size=8192)
    app.add_routes(
        [web.get("/", index), web.get("/status", status), web.post("/pair", pair)]
    )
    return app
