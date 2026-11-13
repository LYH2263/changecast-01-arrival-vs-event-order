<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'

const streams = ref([])
const streamId = ref(null)
const report = ref(null)
const err = ref('')

async function refresh() {
  streams.value = await getJSON('/api/streams')
  if (streamId.value == null && streams.value.length) streamId.value = streams.value[0].id
  report.value = await getJSON(`/api/streams/${streamId.value}/verify`)
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>同源核验</h1>
    <p class="muted">
      投影快照、事件留痕与批次计数三者互证：计数自洽、留痕可重放出快照、单批不混用应用序、
      当前投影等于最近批次快照。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" style="display:flex;flex-wrap:wrap;gap:0.75rem;align-items:flex-end">
      <label>
        投影流
        <select v-model.number="streamId" style="display:block;min-width:18rem;margin-top:0.25rem" @change="refresh">
          <option v-for="s in streams" :key="s.id" :value="s.id">{{ s.title }}</option>
        </select>
      </label>
      <button @click="refresh">重新核验</button>
    </div>

    <div class="panel" v-if="report">
      <p>
        当前应用序 <span class="tag">{{ report.apply_order_label }}</span> ·
        总判定
        <span class="tag" :class="report.ok ? 'ok' : 'danger'">{{ report.ok ? '全部通过' : '存在不一致' }}</span>
      </p>
      <ul class="checklist">
        <li>
          <span class="tag" :class="report.live_check.ok ? 'ok' : 'danger'">
            {{ report.live_check.ok ? '通过' : '失败' }}
          </span>
          <strong>{{ report.live_check.name }}</strong>
          <span class="muted">— {{ report.live_check.detail }}</span>
        </li>
      </ul>
    </div>

    <div class="panel" v-for="b in (report?.batches || [])" :key="b.batch_id">
      <p style="margin:0 0 0.5rem">
        批次 <span class="mono">{{ b.batch_no }}</span>（#{{ b.batch_id }}）·
        <span class="tag">{{ b.apply_order }}</span> · {{ b.kind }} ·
        <span class="tag" :class="b.ok ? 'ok' : 'danger'">{{ b.ok ? '通过' : '失败' }}</span>
      </p>
      <ul class="checklist">
        <li v-for="c in b.checks" :key="c.name">
          <span class="tag" :class="c.ok ? 'ok' : 'danger'">{{ c.ok ? '通过' : '失败' }}</span>
          <strong>{{ c.name }}</strong>
          <span class="muted">— {{ c.detail }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>
