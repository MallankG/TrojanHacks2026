"""Private Supabase checkpoint store, bound to one tenant/run/active lease."""
import asyncio
import base64

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP
from tracelayer_agents import api

from app.core.db import get_db


class SupabaseCheckpointer(BaseCheckpointSaver):
    def __init__(self, tenant_id, run_id, token, thread_id):
        super().__init__(serde=api.checkpoint_serializer())
        self.tenant_id, self.run_id, self.token, self.thread_id = tenant_id, run_id, token, thread_id

    def _scope(self, table, config):
        if config['configurable']['thread_id'] != self.thread_id:
            raise ValueError('Checkpoint thread does not belong to this run')
        return get_db().table(table).select('*').eq('tenant_id', self.tenant_id).eq('run_id', self.run_id).eq(
            'namespace', config['configurable'].get('checkpoint_ns', ''))

    def _dump(self, value):
        kind, data = self.serde.dumps_typed(value)
        if len(data) > 3_000_000:
            raise ValueError('Workflow state exceeds the checkpoint size cap')
        return {'type': kind, 'data': base64.b64encode(data).decode('ascii')}

    def _load(self, value):
        return self.serde.loads_typed((value['type'], base64.b64decode(value['data'], validate=True)))

    def get_tuple(self, config):
        q = self._scope('agent_checkpoints', config)
        if cid := config['configurable'].get('checkpoint_id'):
            q = q.eq('checkpoint_id', cid)
        rows = q.order('checkpoint_id', desc=True).limit(1).execute().data
        if not rows:
            return None
        row = rows[0]
        cfg = {'configurable': {'thread_id': self.thread_id, 'checkpoint_ns': row['namespace'],
                               'checkpoint_id': row['checkpoint_id']}}
        writes = self._scope('agent_checkpoint_writes', cfg).eq('checkpoint_id', row['checkpoint_id']).order(
            'task_id').order('write_index').limit(500).execute().data
        parent = {'configurable': {**cfg['configurable'], 'checkpoint_id': row['parent_id']}} if row['parent_id'] else None
        return CheckpointTuple(config=cfg, checkpoint=self._load(row['checkpoint']), metadata=self._load(row['metadata']),
            parent_config=parent, pending_writes=[(w['task_id'], w['channel'], self._load(w['value'])) for w in writes])

    def put(self, config, checkpoint, metadata, new_versions):
        self._scope('agent_checkpoints', config)
        row = {'tenant_id': self.tenant_id, 'run_id': self.run_id, 'lease_token': self.token,
            'namespace': config['configurable'].get('checkpoint_ns', ''), 'checkpoint_id': checkpoint['id'],
            'parent_id': config['configurable'].get('checkpoint_id'),
            'checkpoint': self._dump(checkpoint), 'metadata': self._dump(metadata)}
        get_db().table('agent_checkpoints').upsert(row, on_conflict='run_id,namespace,checkpoint_id').execute()
        return {'configurable': {'thread_id': self.thread_id, 'checkpoint_ns': row['namespace'],
                                 'checkpoint_id': checkpoint['id']}}

    def put_writes(self, config, writes, task_id, task_path=''):
        self._scope('agent_checkpoint_writes', config)
        for index, (channel, value) in enumerate(writes):
            slot = WRITES_IDX_MAP.get(channel, index)
            row = {'tenant_id': self.tenant_id, 'run_id': self.run_id, 'lease_token': self.token,
                'namespace': config['configurable'].get('checkpoint_ns', ''),
                'checkpoint_id': config['configurable']['checkpoint_id'], 'task_id': task_id,
                'write_index': slot, 'channel': channel, 'value': self._dump(value)}
            get_db().table('agent_checkpoint_writes').upsert(row,
                on_conflict='run_id,namespace,checkpoint_id,task_id,write_index', ignore_duplicates=slot >= 0).execute()

    def list(self, config, *, filter=None, before=None, limit=None):
        if config is None:
            raise ValueError('Explicit run scope required')
        q = self._scope('agent_checkpoints', config)
        if before:
            q = q.lt('checkpoint_id', before['configurable']['checkpoint_id'])
        for row in q.order('checkpoint_id', desc=True).limit(min(limit or 100, 100)).execute().data:
            item = self.get_tuple({'configurable': {**config['configurable'], 'checkpoint_id': row['checkpoint_id']}})
            if item and (not filter or all(item.metadata.get(k) == v for k, v in filter.items())):
                yield item

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=''):
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        for item in await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit))):
            yield item
