"use client";
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useLiveProfile } from '@/lib/live/session';
import { liveApi,useLive } from '@/lib/live/api';
import { LiveData } from '@/features/live/live-state';
import { AlertQueue } from './alert-queue';
import { AlertDetail } from './alert-detail';
import { DEFAULT_ALERT } from '@/lib/seed/alerts';
export function RiskLanding(){
  const profile=useLiveProfile();const router=useRouter();
  const query=useLive(()=>profile?liveApi.alerts({limit:1}):Promise.resolve(null),`risk-entry:${profile?.id}`);
  const id=query.data?.items[0]?.id;
  useEffect(()=>{if(profile && id)router.replace(`/risk/${id}`);},[profile,id,router]);
  if(!profile)return <div className="flex flex-wrap items-start gap-4"><AlertQueue selectedId={DEFAULT_ALERT}/><AlertDetail alertId={DEFAULT_ALERT}/></div>;
  return <LiveData query={query} label="ranked alerts">{page=><p>{page?.items.length?'Opening the highest-ranked live alert…':'No alerts yet. Upload a batch and run analysis to populate your queue.'}</p>}</LiveData>;
}
