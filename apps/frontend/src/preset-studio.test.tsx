import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {describe,expect,it,vi} from 'vitest';
import {SourcePresetStudioPage} from './preset-studio';

function renderPage(){
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  return render(<QueryClientProvider client={client}><SourcePresetStudioPage/></QueryClientProvider>);
}

describe('source preset studio',()=>{
  it('creates a public draft preset from guided no-code settings',async()=>{
    const requests:{path:string;body?:any}[]=[];
    vi.stubGlobal('fetch',vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
      const path=String(input);
      requests.push({path,body:init?.body?JSON.parse(String(init.body)):undefined});
      const payload=path.includes('/projects')?[{id:'project-1',name:'Market'}]
        :path.includes('/presets/blueprints')?[{id:'blueprint-1',name:'Universal v2',project_id:'project-1'}]
        :path.includes('/presets/source')&&init?.method==='POST'?{id:'preset-1',revision:1}
        :[];
      return new Response(JSON.stringify(payload),{status:200,headers:{'Content-Type':'application/json'}});
    }));
    renderPage();
    await waitFor(()=>expect(screen.getByRole('button',{name:'Создать preset'})).toBeEnabled());
    fireEvent.click(screen.getByRole('button',{name:'Создать preset'}));
    await screen.findByLabelText('Название');
    fireEvent.change(screen.getByLabelText('Название'),{target:{value:'Публичные вклады'}});
    fireEvent.change(screen.getByLabelText('Slug'),{target:{value:'public-deposits'}});
    fireEvent.change(screen.getByLabelText('URL источника'),{target:{value:'https://example.test/deposits'}});
    fireEvent.change(screen.getByLabelText('Сегмент источника'),{target:{value:'INDIVIDUAL'}});
    fireEvent.submit(screen.getByRole('button',{name:'Создать draft'}).closest('form')!);
    await waitFor(()=>expect(requests.some(request=>request.path.endsWith('/presets/source')&&request.body)).toBe(true));
    const created=requests.find(request=>request.path.endsWith('/presets/source')&&request.body)?.body;
    expect(created).toMatchObject({
      project_id:'project-1',blueprint_revision_id:'blueprint-1',slug:'public-deposits',status:'DRAFT',source_policy_ref:'public-anonymous-only',
      config_json:{kind:'SourcePreset',nodes:{acquire:{url:'https://example.test/deposits'},process:{operations:[{type:'constant',field:'segment',value:'INDIVIDUAL'}]}}},
    });
  });
});
