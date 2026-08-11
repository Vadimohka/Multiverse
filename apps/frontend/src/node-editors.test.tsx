import {fireEvent,render,screen} from '@testing-library/react';
import {describe,expect,it,vi} from 'vitest';
import {GuidedJsonEditor} from './node-editors';

describe('guided node editors',()=>{
  it('serializes browser actions without changing workflow config shape',()=>{
    const onChange=vi.fn();
    render(<GuidedJsonEditor fieldName="actions" label="Actions" value={[{type:'click',selector:'#old'}]} onChange={onChange}/>);
    fireEvent.change(screen.getByLabelText('action-0-selector'),{target:{value:'#new'}});
    expect(onChange).toHaveBeenLastCalledWith([{type:'click',selector:'#new'}]);
  });

  it('serializes timestamp range controls as the backend config object',()=>{
    const onChange=vi.fn();
    render(<GuidedJsonEditor fieldName="date_range_query" label="Date range" value={{from_param:'from',to_param:'to',timezone:'UTC'}} onChange={onChange}/>);
    fireEvent.change(screen.getByLabelText('Timezone'),{target:{value:'Europe/Minsk'}});
    expect(onChange).toHaveBeenLastCalledWith({from_param:'from',to_param:'to',timezone:'Europe/Minsk'});
  });
});
