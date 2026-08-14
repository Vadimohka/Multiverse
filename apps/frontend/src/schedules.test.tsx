import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {describe,expect,it,vi} from 'vitest';
import {SchedulesPage} from './pages';

describe('schedules page',()=>{
  it('edits an existing schedule without recreating it',async()=>{
    const requests:{path:string;method?:string;body?:any}[]=[];
    vi.stubGlobal('fetch',vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
      const path=String(input);requests.push({path,method:init?.method,body:init?.body?JSON.parse(String(init.body)):undefined});
      const body=path.endsWith('/schedules')?[{id:'schedule-1',workflow_id:'workflow-1',name:'Market',cron:'0 8 * * 1',timezone:'Europe/Minsk',enabled:false}]:path.endsWith('/workflows')?[{id:'workflow-1',name:'Market workflow'}]:{id:'schedule-1',cron:'30 9 * * 1-5',enabled:true};
      return new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}});
    }));
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    render(<QueryClientProvider client={client}><SchedulesPage/></QueryClientProvider>);
    await screen.findByText('Market');
    fireEvent.click(screen.getByRole('button',{name:'Изменить Market'}));
    fireEvent.change(screen.getByLabelText('Cron'),{target:{value:'30 9 * * 1-5'}});
    fireEvent.click(screen.getByLabelText('Включено'));
    fireEvent.submit(screen.getByRole('button',{name:'Сохранить расписание'}).closest('form')!);
    await waitFor(()=>expect(requests.some(request=>request.path.endsWith('/schedules/schedule-1')&&request.method==='PATCH')).toBe(true));
  });
});
