<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getJSON, postJSON } from '../api'

const route = useRoute()
const streams = ref([])
const streamId = ref(null)
const preview = ref(null)
const snapshots = ref(null)
const err = ref('')
const msg = ref('')
const busy = ref(false)

const current = computed(() => streams.value.find((s) => s.id === Number(streamId.value)))

async function refresh() {
  streams.value = await getJSON('/api/streams')
  if (streamId.value == null && streams.value.length) streamId.value = streams.value[0].id
}

async function loadAll() {
  if (!streamId.value) return
  const q = `stream_id=${streamId.value}`
  preview.value = await getJSON(`/api/streams/${streamId.value}/compare-orders`)
  snapshots.value = await getJSON(`/api/streams/${streamId.value}/compare-batches`)
}

async function switchTo(order) {
  err.value = ''
  msg.value = ''
  busy.value = true
  try {
    const r = await postJSON(`/api/streams/${streamId.value}/apply-order`, { apply_order: order, note: '对照页切换' })
    msg.value =
      `已切到「${r.to_order_label}」` +
      (r.buffered_settled ? `；旧策略清算缓冲 ${r.buffered_settled} 条（批次 #${r.drain_batch_id}）` : '') +
      (r.running_failed ? `；失败未完成批次 ${r.running_failed} 个` : '') +
      (r.rebuild_batch_id ? `；已按新策略重建（批次 #${r.rebuild_batch_id}）` : '')
    await refresh()
    await loadAll()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function runCurrent() {
  err.value = ''
  busy.value = true
  try {
    await postJSON('/api/project', { stream_id: Number(streamId.value), note: '对照页跑一趟' })
    msg.value = '已按当前应用序跑完一趟'
    await refresh()
    await loadAll()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

function ver(order) {
  return snapshots.value?.verifications?.[order]
}

function orderSide(side) {
  return preview.value?.[side]
}

onMounted(async () => {
  try {
    await refresh()
    if (route.query.stream_id) streamId.value = Number(route.query.stream_id)
    await loadAll()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

watch(streamId, async () => {
  try { await loadAll() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>应用序对照</h1>
    <p class="muted">
      同一夹具分别按两套互斥应用序计算终态，逐实体对比载荷与 applied_seq。
      上半部分是只读预览（不落库），下半部分对照两种策略各自最近一批的真实批次与核验。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="msg" class="tag ok">{{ msg }}</p>

    <div class="panel" style="display:flex;flex-wrap:wrap;gap:0.75rem;align-items:flex-end">
      <label>
        投影流
        <select v-model.number="streamId" style="display:block;min-width:18rem;margin-top:0.25rem">
          <option v-for="s in streams" :key="s.id" :value="s.id">{{ s.title }}</option>
        </select>
      </label>
      <button :disabled="busy" @click="runCurrent">按当前序跑一趟</button>
      <button :disabled="busy" @click="switchTo('by_seq')">切到 seq 序</button>
      <button :disabled="busy" @click="switchTo('by_arrival')">切到到达序</button>
    </div>

    <div class="panel" v-if="current">
      <p style="margin:0">
        当前应用序 <span class="tag">{{ current.apply_order_label }}</span>
        · 缓冲中 <span class="tag" :class="current.open_buffer_count ? 'warn' : ''">{{ current.open_buffer_count }}</span>
        · <router-link :to="{ path: '/buffer', query: { stream_id: current.id } }">查看缓冲区</router-link>
      </p>
    </div>

    <div class="panel" v-if="preview">
      <h3 style="margin-top:0">同夹具两种序的终态（只读预览）</h3>
      <p>
        结论：<span class="tag" :class="preview.same_terminal_state ? 'ok' : 'warn'">
          {{ preview.same_terminal_state ? '两种序终态一致' : `两种序终态不同，${preview.diff.length} 个实体分歧` }}
        </span>
      </p>
      <div class="col-grid">
        <div v-for="side in ['by_seq', 'by_arrival']" :key="side" class="col-box">
          <h4>{{ orderSide(side).apply_order_label }}</h4>
          <p class="muted mono" style="font-size:0.78rem;word-break:break-all">
            应用顺序：{{ orderSide(side).order.join(' → ') }}
          </p>
          <p class="muted">
            写入 {{ orderSide(side).counts.applied_upsert }} · 删除 {{ orderSide(side).counts.applied_delete }}
            · 存活 {{ orderSide(side).counts.projected_alive }} · 已删 {{ orderSide(side).counts.projected_deleted }}
          </p>
          <table>
            <thead><tr><th>实体</th><th>seq</th><th>事件</th><th>载荷</th></tr></thead>
            <tbody>
              <tr v-for="r in orderSide(side).projection" :key="r.entity_key">
                <td class="mono">{{ r.entity_key }}</td>
                <td class="mono">{{ r.applied_seq }}</td>
                <td class="mono">{{ r.applied_event_id }}</td>
                <td class="mono">
                  <span v-if="r.deleted" class="tag danger">已删</span>
                  <span v-else>{{ JSON.stringify(r.payload) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <h4 style="margin-top:1.25rem">分歧明细</h4>
      <table>
        <thead><tr><th>实体</th><th>分歧类型</th><th>seq 序终态</th><th>到达序终态</th></tr></thead>
        <tbody>
          <tr v-for="d in preview.diff" :key="d.entity_key">
            <td class="mono">{{ d.entity_key }}</td>
            <td><span class="tag warn">{{ d.kind }}</span></td>
            <td class="mono">{{ d.left ? `${d.left.applied_event_id} / seq ${d.left.applied_seq} / ${d.left.deleted ? '已删' : JSON.stringify(d.left.payload)}` : '—' }}</td>
            <td class="mono">{{ d.right ? `${d.right.applied_event_id} / seq ${d.right.applied_seq} / ${d.right.deleted ? '已删' : JSON.stringify(d.right.payload)}` : '—' }}</td>
          </tr>
          <tr v-if="!preview.diff.length"><td colspan="4" class="muted">无分歧。</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel" v-if="snapshots">
      <h3 style="margin-top:0">两种策略各自的真实批次（同夹具各跑一趟）</h3>
      <p v-if="snapshots.same_terminal_state === null" class="muted">
        还缺一种应用序的批次。分别在两种序下各跑一趟即可对照。
      </p>
      <p v-else>
        结论：<span class="tag" :class="snapshots.same_terminal_state ? 'ok' : 'warn'">
          {{ snapshots.same_terminal_state ? '两批终态一致' : `两批终态不同，${snapshots.diff.length} 个实体分歧` }}
        </span>
      </p>
      <div class="col-grid">
        <div v-for="side in ['by_seq', 'by_arrival']" :key="side" class="col-box">
          <h4>{{ side === 'by_seq' ? '按事件 seq 升序投影' : '按夹具到达顺序依次投影' }}</h4>
          <template v-if="snapshots.batches[side]">
            <p class="mono" style="font-size:0.82rem">
              {{ snapshots.batches[side].batch_no }} · {{ snapshots.batches[side].kind }} ·
              {{ snapshots.batches[side].status }}
            </p>
            <p class="muted">
              源事件 {{ snapshots.batches[side].source_events }} ·
              写入 {{ snapshots.batches[side].applied_upsert }} ·
              删除 {{ snapshots.batches[side].applied_delete }} ·
              存活 {{ snapshots.batches[side].projected_alive }} ·
              已删 {{ snapshots.batches[side].projected_deleted }}
            </p>
            <p v-if="ver(side)">
              同源核验 <span class="tag" :class="ver(side).ok ? 'ok' : 'danger'">{{ ver(side).ok ? '通过' : '失败' }}</span>
            </p>
            <table>
              <thead><tr><th>实体</th><th>seq</th><th>事件</th><th>载荷</th></tr></thead>
              <tbody>
                <tr v-for="r in snapshots.snapshots[side]" :key="r.entity_key">
                  <td class="mono">{{ r.entity_key }}</td>
                  <td class="mono">{{ r.applied_seq }}</td>
                  <td class="mono">{{ r.applied_event_id }}</td>
                  <td class="mono">
                    <span v-if="r.deleted" class="tag danger">已删</span>
                    <span v-else>{{ JSON.stringify(r.payload) }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </template>
          <p v-else class="muted">尚无该应用序的批次。</p>
        </div>
      </div>
      <h4 style="margin-top:1.25rem">批次分歧明细</h4>
      <table>
        <thead><tr><th>实体</th><th>分歧类型</th><th>seq 序批次</th><th>到达序批次</th></tr></thead>
        <tbody>
          <tr v-for="d in snapshots.diff" :key="d.entity_key">
            <td class="mono">{{ d.entity_key }}</td>
            <td><span class="tag warn">{{ d.kind }}</span></td>
            <td class="mono">{{ d.left ? `${d.left.applied_event_id} / seq ${d.left.applied_seq} / ${d.left.deleted ? '已删' : JSON.stringify(d.left.payload)}` : '—' }}</td>
            <td class="mono">{{ d.right ? `${d.right.applied_event_id} / seq ${d.right.applied_seq} / ${d.right.deleted ? '已删' : JSON.stringify(d.right.payload)}` : '—' }}</td>
          </tr>
          <tr v-if="snapshots.same_terminal_state !== null && !snapshots.diff.length">
            <td colspan="4" class="muted">无分歧。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
