<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getJSON } from '../api'

const route = useRoute()
const rows = ref([])
const batches = ref([])
const batchId = ref(null)
const batchInfo = ref(null)
const err = ref('')

async function refresh() {
  const q = new URLSearchParams()
  if (batchId.value) q.set('batch_id', String(batchId.value))
  rows.value = await getJSON('/api/events?' + q.toString())
  batchInfo.value = batchId.value
    ? await getJSON('/api/batches/' + batchId.value)
    : null
}

onMounted(async () => {
  try {
    batches.value = await getJSON('/api/batches?limit=50')
    if (route.query.batch_id) batchId.value = Number(route.query.batch_id)
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

watch(batchId, async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>事件留痕</h1>
    <p class="muted">
      每条已处理事件都写明归属批次、应用序、到达序与执行序，可据此重放出该批终态。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" style="display:flex;flex-wrap:wrap;gap:0.75rem;align-items:flex-end">
      <label>
        批次
        <select v-model="batchId" style="display:block;min-width:22rem;margin-top:0.25rem">
          <option :value="null">全部</option>
          <option v-for="b in batches" :key="b.id" :value="b.id">
            #{{ b.id }} · {{ b.batch_no }} · {{ b.stream_title }} · {{ b.apply_order_label }} · {{ b.kind }}
          </option>
        </select>
      </label>
      <button @click="batchId = null">看全部</button>
    </div>

    <div class="panel" v-if="batchInfo">
      <p style="margin:0">
        本批应用序 <span class="tag">{{ batchInfo.apply_order_label }}</span>
        · 源事件 {{ batchInfo.counts.source_events }} · 写入 {{ batchInfo.counts.applied_upsert }}
        · 删除 {{ batchInfo.counts.applied_delete }} · 挂起 {{ batchInfo.counts.held_events }}
      </p>
      <p v-if="batchInfo.note" class="muted" style="margin:0.4rem 0 0">{{ batchInfo.note }}</p>
    </div>

    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>批次</th><th>应用序</th><th>执行序</th><th>到达序</th>
            <th>event_id</th><th>实体键</th><th>op</th><th>seq</th><th>事件时间</th><th>载荷</th><th>结果</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in rows" :key="e.id">
            <td>{{ e.batch_id }}</td>
            <td><span class="tag">{{ e.apply_order }}</span></td>
            <td class="mono">{{ e.applied_index }}</td>
            <td class="mono">{{ e.arrival_index }}</td>
            <td class="mono">{{ e.event_id }}</td>
            <td class="mono">{{ e.entity_key }}</td>
            <td class="mono">{{ e.op }}</td>
            <td class="mono">{{ e.seq }}</td>
            <td class="mono">{{ e.event_time }}</td>
            <td class="mono">{{ e.payload == null ? '—' : JSON.stringify(e.payload) }}</td>
            <td><span class="tag">{{ e.result }}</span></td>
          </tr>
          <tr v-if="!rows.length"><td colspan="11" class="muted">暂无留痕。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
