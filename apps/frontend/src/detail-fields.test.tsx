import {fireEvent,render,screen} from '@testing-library/react';
import {describe,expect,it,vi} from 'vitest';
import {DetailFieldsEditor} from './workflow-editor';

describe('detail field source editor',()=>{
  it('configures a timestamp from a generic listing item path',()=>{
    const onChange=vi.fn();
    render(<DetailFieldsEditor
      value={[{id:'published',name:'source_published_at',source:'listing',source_path:'shortDate',timezone:'Europe/Minsk'}]}
      onChange={onChange}
      onSelector={vi.fn()}
    />);

    expect(screen.getByRole('option',{name:'Listing item'})).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('source_published_at source path'),{target:{value:'metadata.published_at'}});
    expect(onChange).toHaveBeenLastCalledWith([
      {id:'published',name:'source_published_at',source:'listing',source_path:'metadata.published_at',timezone:'Europe/Minsk'},
    ]);
  });
});
