import {useCallback, useEffect, useRef, useState} from 'react';
import {api} from './api';

export function useCatalog<T extends {id:string}>(path:string){
 const [items,setItems]=useState<T[]>([]);const [cursor,setCursor]=useState<string|null>(null);
 const [loading,setLoading]=useState(false);const [error,setError]=useState('');
 const generation=useRef(0);const request=useRef<AbortController|null>(null);
 const fetchPage=useCallback(async(next?:string)=>{
  request.current?.abort();const controller=new AbortController();request.current=controller;
  const seq=++generation.current;setLoading(true);
  try{const result=await api<{items:T[];next_cursor:string|null}>(path+(next?`${path.includes('?')?'&':'?'}cursor=${encodeURIComponent(next)}`:''),'GET',undefined,undefined,{signal:controller.signal});
   if(seq===generation.current){setItems(old=>next?[...old,...result.items.filter(row=>!old.some(item=>item.id===row.id))]:result.items);setCursor(result.next_cursor);setError('')}
  }catch(e){if(seq===generation.current&&!controller.signal.aborted)setError((e as Error).message)}
  finally{if(seq===generation.current)setLoading(false)}
 },[path]);
 useEffect(()=>{setItems([]);setCursor(null);void fetchPage();return()=>{generation.current++;request.current?.abort()}},[fetchPage]);
 return {items,cursor,loading,error,reload:()=>fetchPage(),more:()=>cursor&&!loading?fetchPage(cursor):undefined};
}
