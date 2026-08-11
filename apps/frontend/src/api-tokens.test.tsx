import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import {api} from './api';
import {ApiTokensPage} from './api-tokens';

vi.mock('./api',()=>({api:vi.fn()}));
const mockedApi=vi.mocked(api);

function renderPage(){const client=new QueryClient({defaultOptions:{queries:{retry:false}}});return render(<QueryClientProvider client={client}><ApiTokensPage/></QueryClientProvider>)}

describe('API token management',()=>{
  beforeEach(()=>{mockedApi.mockReset();localStorage.clear()});

  it('shows a scoped token once and never persists its clear value',async()=>{
    mockedApi.mockImplementation(async(path,options)=>{
      if(path==='/datasets')return [{id:'dataset-1',name:'News'}] as never;
      if(path==='/api-tokens'&&options?.method==='POST')return {id:'token-1',name:'Agent',token:'mv_clear_secret',token_prefix:'mv_clear',dataset_ids:['dataset-1'],scopes:['datasets:read'],rate_limit_per_minute:120} as never;
      if(path==='/api-tokens')return [] as never;
      throw new Error(String(path));
    });
    Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:vi.fn().mockResolvedValue(undefined)}});
    renderPage();
    await screen.findByText('API-токены');
    fireEvent.click(screen.getByText('Создать токен'));
    fireEvent.change(screen.getByPlaceholderText('AI news reader'),{target:{value:'Agent'}});
    fireEvent.click(screen.getByText('News'));
    fireEvent.click(screen.getByText('Создать'));
    expect(await screen.findByTestId('clear-api-token')).toHaveTextContent('mv_clear_secret');
    fireEvent.click(screen.getByText('Копировать'));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('mv_clear_secret');
    expect(localStorage.getItem('api_token')).toBeNull();
  });

  it('revokes an active token',async()=>{
    mockedApi.mockImplementation(async(path,options)=>{
      if(path==='/datasets')return [] as never;
      if(path==='/api-tokens/token-1'&&options?.method==='DELETE')return undefined as never;
      if(path==='/api-tokens')return [{id:'token-1',name:'Agent',token_prefix:'mv_agent',dataset_ids:['dataset-1'],scopes:['datasets:read'],rate_limit_per_minute:120,expires_at:null,last_used_at:null,revoked_at:null,created_at:'2026-08-11T00:00:00Z'}] as never;
      throw new Error(String(path));
    });
    vi.spyOn(window,'confirm').mockReturnValue(true);
    renderPage();
    fireEvent.click(await screen.findByText('Отозвать Agent'));
    await waitFor(()=>expect(mockedApi).toHaveBeenCalledWith('/api-tokens/token-1',{method:'DELETE'}));
  });
});
