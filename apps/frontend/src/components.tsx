import {FormEvent, ReactNode, useState} from 'react';

export function Header({title, subtitle='Управление сбором, проверкой и публикацией данных', actions}:{title:string; subtitle?:string; actions?:ReactNode}){
  return <header><div><h1>{title}</h1><p>{subtitle}</p></div>{actions}</header>;
}

export function Loading(){return <div className="panel">Загрузка…</div>}

export function ErrorPanel({error}:{error:unknown}){return <div className="panel error">{error instanceof Error?error.message:String(error)}</div>}

export function Modal({title,onClose,children,wide=false}:{title:string;onClose:()=>void;children:ReactNode;wide?:boolean}){
  return <div className="modal-backdrop" onMouseDown={onClose}><div className={`modal ${wide?'modal-wide':''}`} onMouseDown={event=>event.stopPropagation()}><div className="modal-head"><h2>{title}</h2><button type="button" className="icon-button secondary" onClick={onClose}>×</button></div>{children}</div></div>;
}

export function Table({rows,columns,onRowClick,empty='Нет данных'}:{rows:any[];columns?:string[];onRowClick?:(row:any)=>void;empty?:string}){
  const cols=columns||(rows[0]?Object.keys(rows[0]):[]);
  if(!rows.length)return <div className="empty">{empty}</div>;
  return <div className="table-wrap"><table><thead><tr>{cols.map(column=><th key={column}>{humanize(column)}</th>)}</tr></thead><tbody>{rows.map((row,index)=><tr key={row.id||index} onClick={()=>onRowClick?.(row)} className={onRowClick?'clickable':''}>{cols.map(column=><td key={column} title={display(row[column])}>{display(row[column])}</td>)}</tr>)}</tbody></table></div>;
}

function compactJsonPreview(value:any,depth=0):any{
  if(typeof value==='string')return value.length>10_000?`${value.slice(0,10_000)}\n… string preview truncated (${value.length.toLocaleString()} characters total).`:value;
  if(value===null||typeof value!=='object')return value;
  if(depth>=8)return '… nested value omitted from preview';
  if(Array.isArray(value)){
    const items=value.slice(0,100).map(item=>compactJsonPreview(item,depth+1));
    return value.length>100?[...items,`… ${value.length-100} more array items omitted from preview`]:items;
  }
  const entries=Object.entries(value).slice(0,100).map(([key,item])=>[key,compactJsonPreview(item,depth+1)]);
  const compact=Object.fromEntries(entries);
  if(Object.keys(value).length>100)compact.__preview__=`… ${Object.keys(value).length-100} more object keys omitted from preview`;
  return compact;
}

export function JsonView({value,maxHeight=420,maxChars=200_000}:{value:any;maxHeight?:number;maxChars?:number}){
  const serialized=JSON.stringify(compactJsonPreview(value),null,2);
  const preview=serialized.length>maxChars?`${serialized.slice(0,maxChars)}\n\n… JSON preview truncated.`:serialized;
  return <pre style={{maxHeight}}>{preview}</pre>
}

export function ConfirmButton({children,onConfirm,className}:{children:ReactNode;onConfirm:()=>void|Promise<void>;className?:string}){
  const [busy,setBusy]=useState(false);
  async function click(){if(!window.confirm('Подтвердить действие?'))return;setBusy(true);try{await onConfirm()}finally{setBusy(false)}}
  return <button type="button" className={className} disabled={busy} onClick={click}>{busy?'Выполняется…':children}</button>;
}

export function SubmitButton({children='Сохранить'}:{children?:ReactNode}){return <button type="submit">{children}</button>}

export function useFormError(){const [error,setError]=useState('');function wrap(handler:(event:FormEvent<HTMLFormElement>)=>Promise<void>){return async(event:FormEvent<HTMLFormElement>)=>{event.preventDefault();setError('');try{await handler(event)}catch(value){setError(value instanceof Error?value.message:String(value))}}}return {error,setError,wrap}}

export function StatusBadge({value}:{value:string|undefined}){const normalized=(value||'UNKNOWN').toLowerCase();return <span className={`badge badge-${normalized}`}>{value||'—'}</span>}

export function humanize(value:string){const labels:Record<string,string>={name:'Название',slug:'Slug',status:'Статус',created_at:'Создано',updated_at:'Обновлено',entry_url:'URL',source_type:'Тип',fetch_mode:'Загрузка',access_status:'Доступ',enabled:'Активен',version:'Версия',published_version:'Опубликовано',workflow_id:'Workflow',workflow_name:'Workflow',started_at:'Начало',finished_at:'Завершение',project_id:'Проект',model:'Модель',provider:'Provider',description:'Описание',default_timezone:'Часовой пояс',source_url:'Источник',observed_at:'Собрано',review_status:'Проверка',confidence:'Confidence',natural_key:'Ключ'};return labels[value]||value.replaceAll('_',' ')}

function display(value:any):string{
  if(value===null||value===undefined||value==='')return '—';
  if(typeof value==='boolean')return value?'Да':'Нет';
  if(typeof value==='object')return JSON.stringify(value);
  if(typeof value==='string'&&/^\d{4}-\d{2}-\d{2}T/.test(value))return new Date(value).toLocaleString('ru-RU');
  return String(value);
}
