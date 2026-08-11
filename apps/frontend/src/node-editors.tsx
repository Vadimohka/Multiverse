import {useEffect,useState} from 'react';

type Props={fieldName:string;label:string;value:unknown;onChange:(value:unknown)=>void};

function parseValue(value:string):unknown{
  const text=value.trim();
  if(!text)return '';
  try{return JSON.parse(text)}catch{return value}
}

export function BrowserActionsEditor({value,onChange}:{value:unknown;onChange:(value:unknown)=>void}){
  const actions=Array.isArray(value)?value as Record<string,unknown>[]:[];
  function change(index:number,key:string,next:unknown){onChange(actions.map((item,itemIndex)=>itemIndex===index?{...item,[key]:next}:item))}
  return <div className="editor-list"><div className="editor-list-head"><strong>Действия браузера</strong><button type="button" className="secondary small" onClick={()=>onChange([...actions,{type:'click',selector:''}])}>Добавить</button></div>{actions.map((action,index)=><div className="editor-row" key={index}><select aria-label={`action-${index}-type`} value={String(action.type||'click')} onChange={event=>change(index,'type',event.target.value)}>{['click','fill','select','hover','press','wait','wait_for','scroll','javascript'].map(type=><option key={type}>{type}</option>)}</select>{!['wait','scroll','javascript'].includes(String(action.type))&&<input aria-label={`action-${index}-selector`} placeholder="CSS selector" value={String(action.selector||'')} onChange={event=>change(index,'selector',event.target.value)}/>} {['fill','select','press'].includes(String(action.type))&&<input aria-label={`action-${index}-value`} placeholder="Значение" value={String(action.value||'')} onChange={event=>change(index,'value',event.target.value)}/>} {action.type==='wait'&&<input type="number" aria-label={`action-${index}-seconds`} value={Number(action.seconds||1)} onChange={event=>change(index,'seconds',Number(event.target.value))}/>} {action.type==='scroll'&&<input type="number" aria-label={`action-${index}-pixels`} value={Number(action.pixels||1000)} onChange={event=>change(index,'pixels',Number(event.target.value))}/>} {action.type==='javascript'&&<textarea aria-label={`action-${index}-script`} value={String(action.script||'')} onChange={event=>change(index,'script',event.target.value)}/>}<button type="button" className="danger small" onClick={()=>onChange(actions.filter((_,itemIndex)=>itemIndex!==index))}>×</button></div>)}</div>
}

function DateRangeEditor({value,onChange}:{value:unknown;onChange:(value:unknown)=>void}){
  const item=value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};
  const set=(key:string,next:unknown)=>onChange({...item,[key]:next});
  return <div className="editor-list"><strong>Диапазон времени источника</strong><div className="editor-grid"><label>Query от<input value={String(item.from_param||'')} onChange={event=>set('from_param',event.target.value)}/></label><label>Query до<input value={String(item.to_param||'')} onChange={event=>set('to_param',event.target.value)}/></label><label>Дней назад<input type="number" value={Number(item.lookback_days||0)} onChange={event=>set('lookback_days',Number(event.target.value))}/></label><label>Формат<input value={String(item.format||'YYYY-MM-DD')} onChange={event=>set('format',event.target.value)}/></label><label>Timezone<input value={String(item.timezone||'UTC')} onChange={event=>set('timezone',event.target.value)}/></label></div></div>
}

function PrimitiveListEditor({value,onChange}:{value:unknown[];onChange:(value:unknown)=>void}){
  return <div className="editor-list">{value.map((item,index)=><div className="editor-row" key={index}><input aria-label={`list-item-${index}`} value={typeof item==='string'?item:JSON.stringify(item)} onChange={event=>onChange(value.map((entry,itemIndex)=>itemIndex===index?parseValue(event.target.value):entry))}/><button type="button" className="danger small" onClick={()=>onChange(value.filter((_,itemIndex)=>itemIndex!==index))}>×</button></div>)}<button type="button" className="secondary small" onClick={()=>onChange([...value,''])}>Добавить значение</button></div>
}

function ObjectEditor({value,onChange}:{value:Record<string,unknown>;onChange:(value:unknown)=>void}){
  const entries=Object.entries(value);
  function replace(index:number,key:string,next:unknown){const updated=entries.map((entry,itemIndex)=>itemIndex===index?[key,next] as [string,unknown]:entry);onChange(Object.fromEntries(updated.filter(([name])=>name)))}
  return <div className="editor-list">{entries.map(([key,item],index)=><div className="editor-row" key={`${key}-${index}`}><input aria-label={`object-key-${index}`} placeholder="Ключ" value={key} onChange={event=>replace(index,event.target.value,item)}/><input aria-label={`object-value-${index}`} placeholder="Значение" value={typeof item==='string'?item:JSON.stringify(item)} onChange={event=>replace(index,key,parseValue(event.target.value))}/><button type="button" className="danger small" onClick={()=>onChange(Object.fromEntries(entries.filter((_,itemIndex)=>itemIndex!==index)))}>×</button></div>)}<button type="button" className="secondary small" onClick={()=>onChange({...value,[`field_${entries.length+1}`]:''})}>Добавить поле</button></div>
}

function AdvancedJson({value,onChange}:{value:unknown;onChange:(value:unknown)=>void}){
  const [text,setText]=useState(JSON.stringify(value,null,2));const [error,setError]=useState('');
  useEffect(()=>setText(JSON.stringify(value,null,2)),[value]);
  function commit(){try{onChange(text.trim()?JSON.parse(text):{});setError('')}catch{setError('Некорректный JSON')}}
  return <details><summary>Расширенный JSON</summary><textarea className="code-input" aria-label="advanced-json" value={text} onChange={event=>setText(event.target.value)} onBlur={commit}/>{error&&<span className="field-error">{error}</span>}</details>
}

export function GuidedJsonEditor({fieldName,label,value,onChange}:Props){
  if(fieldName==='actions')return <label>{label}<BrowserActionsEditor value={value} onChange={onChange}/><AdvancedJson value={value} onChange={onChange}/></label>;
  if(fieldName==='date_range_query')return <label>{label}<DateRangeEditor value={value} onChange={onChange}/><AdvancedJson value={value} onChange={onChange}/></label>;
  const normalized=value??(fieldName.endsWith('s')?[]:{});
  return <label>{label}{Array.isArray(normalized)?<PrimitiveListEditor value={normalized} onChange={onChange}/>:normalized&&typeof normalized==='object'?<ObjectEditor value={normalized as Record<string,unknown>} onChange={onChange}/>:<input value={String(normalized)} onChange={event=>onChange(parseValue(event.target.value))}/>}<AdvancedJson value={normalized} onChange={onChange}/></label>
}
