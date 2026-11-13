<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
import { useRoute } from 'vue-router'
const route = useRoute()
const rows = ref([])
const streams = ref({})
const streamId = ref(null)
const err = ref('')
async function refresh() {
  const q = streamId.value ? `?stream_id=${streamId.value}` : ''
  rows.value = await getJSON('/api/ooo-buffer' + q)
}
onMounted(async () => {
  try {
    const list = await getJSON('/api/streams')
    streams.value = Object.fromEntries(list.map((s) => [s.id, s.title]))
    if (route.query.stream_id) streamId.value = Number(route.query.stream_id)
    await refresh()
  } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>缓冲区</h1>
    <p class="muted">
      晚到事件暂存区。底座不会自动吞掉缓冲：要么随下一趟按<strong>当前应用序</strong>一并清算，
      要么在切换策略时由<strong>旧策略</strong>的独立批次清算，两种路径都单独留痕。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" style="display:flex;gap:0.75rem;align-items:flex-end">
      <label>
        投影流
        <select v-model="streamId" style="display:block;min-width:16rem;margin-top:0.25rem" @change="refresh">
          <option :value="null">全部</option>
          <option v-for="(title, id) in streams" :key="id" :value="Number(id)">{{ title }}</option>
        </select>
      </label>
      <button @click="refresh">刷新</button>
    </div>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>流</th><th>event_id</th><th>实体键</th><th>op</th><th>seq</th><th>到达序</th>
            <th>状态</th><th>清算批次</th><th>备注</th><th>入缓时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>{{ r.stream_title || r.stream_id }}</td>
            <td class="mono">{{ r.event_id }}</td>
            <td class="mono">{{ r.entity_key }}</td>
            <td class="mono">{{ r.op }}</td>
            <td class="mono">{{ r.seq }}</td>
            <td class="mono">{{ r.arrival_index }}</td>
            <td><span class="tag" :class="r.status === 'open' ? 'warn' : 'ok'">{{ r.status }}</span></td>
            <td class="mono">{{ r.drained_batch_id ?? '—' }}</td>
            <td class="muted" style="font-size:0.82rem">{{ r.note || '—' }}</td>
            <td class="mono">{{ r.buffered_at }}</td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="10" class="muted">当前无缓冲事件。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
