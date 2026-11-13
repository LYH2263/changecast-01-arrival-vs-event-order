<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const streams = ref([])
const batches = ref([])
const streamId = ref(null)
const note = ref('')
const holdLate = ref(false)
const last = ref(null)
const verify = ref(null)
const busy = ref(false)
const err = ref('')

async function refresh() {
  streams.value = await getJSON('/api/streams')
  batches.value = await getJSON('/api/batches?limit=20')
  if (streamId.value == null && streams.value.length) {
    streamId.value = streams.value[0].id
  }
}

const currentStream = () => streams.value.find((s) => s.id === Number(streamId.value))

async function run() {
  err.value = ''
  busy.value = true
  verify.value = null
  try {
    last.value = await postJSON('/api/project', {
      stream_id: Number(streamId.value),
      note: note.value || null,
      hold_late: holdLate.value,
    })
    verify.value = await getJSON(`/api/batches/${last.value.id}/verify`)
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>投影台</h1>
    <p class="muted">
      读取 CDC 夹具，按<strong>该流当前选定的唯一应用序</strong>投影；本批绝不混用另一套排序。
      「挂起晚到」仅把按到达序判定为晚到的事件放进缓冲，供演示切换路径。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <h3 style="margin-top:0">发起投影</h3>
      <div style="display:flex;flex-wrap:wrap;gap:0.75rem;align-items:flex-end">
        <label>
          投影流
          <select v-model.number="streamId" style="display:block;min-width:18rem;margin-top:0.25rem">
            <option v-for="s in streams" :key="s.id" :value="s.id">
              {{ s.title }}（{{ s.apply_order_label }}）
            </option>
          </select>
        </label>
        <label>
          备注（可选）
          <input v-model="note" style="display:block;width:16rem;margin-top:0.25rem" placeholder="本趟说明" />
        </label>
        <label style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.45rem">
          <input type="checkbox" v-model="holdLate" />
          挂起晚到事件到缓冲
        </label>
        <button :disabled="busy || !streamId" @click="run">{{ busy ? '投影中…' : '应用变更流' }}</button>
      </div>
      <p v-if="currentStream()" style="margin:0.75rem 0 0">
        当前应用序 <span class="tag">{{ currentStream().apply_order_label }}</span>
        · 缓冲中 <span class="tag" :class="currentStream().open_buffer_count ? 'warn' : ''">{{ currentStream().open_buffer_count }}</span>
        · <router-link :to="{ path: '/streams' }">去投影流切换策略</router-link>
      </p>
    </div>

    <div class="panel" v-if="last">
      <h3 style="margin-top:0">最近一趟</h3>
      <p>
        批次 <span class="mono">{{ last.batch_no }}</span> · {{ last.stream_title }} ·
        <span class="tag ok">{{ last.status }}</span>
        · 应用序 <span class="tag">{{ last.apply_order_label }}</span>
        · 类型 <span class="mono">{{ last.kind }}</span>
      </p>
      <div class="stat-grid">
        <div class="stat"><div class="stat-num">{{ last.counts.source_events }}</div><div class="stat-label">源事件</div></div>
        <div class="stat"><div class="stat-num">{{ last.counts.applied_upsert }}</div><div class="stat-label">写入</div></div>
        <div class="stat"><div class="stat-num">{{ last.counts.applied_delete }}</div><div class="stat-label">删除</div></div>
        <div class="stat"><div class="stat-num">{{ last.counts.skipped_duplicate }}</div><div class="stat-label">跳过重复</div></div>
        <div class="stat"><div class="stat-num">{{ last.counts.held_events }}</div><div class="stat-label">挂起缓冲</div></div>
        <div class="stat"><div class="stat-num">{{ last.counts.projected_alive }}</div><div class="stat-label">存活</div></div>
        <div class="stat"><div class="stat-num">{{ last.counts.projected_deleted }}</div><div class="stat-label">已删</div></div>
      </div>
      <p v-if="last.note" class="muted" style="margin-top:0.6rem">{{ last.note }}</p>

      <div v-if="verify" style="margin-top:0.85rem">
        <p>
          同源核验：
          <span class="tag" :class="verify.ok ? 'ok' : 'danger'">{{ verify.ok ? '全部通过' : '存在不一致' }}</span>
        </p>
        <ul class="checklist">
          <li v-for="c in verify.checks" :key="c.name">
            <span class="tag" :class="c.ok ? 'ok' : 'danger'">{{ c.ok ? '通过' : '失败' }}</span>
            <strong>{{ c.name }}</strong>
            <span class="muted">— {{ c.detail }}</span>
          </li>
        </ul>
      </div>

      <p style="margin-top:0.75rem">
        <router-link :to="{ path: '/projections', query: { stream_id: last.stream_id } }">查看投影快照 →</router-link>
        ·
        <router-link :to="{ path: '/events', query: { batch_id: last.id } }">事件留痕 →</router-link>
      </p>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">历史批次</h3>
      <table>
        <thead>
          <tr>
            <th>批次</th><th>投影流</th><th>类型</th><th>应用序</th><th>状态</th>
            <th>源事件</th><th>写入</th><th>删除</th><th>挂起</th><th>存活</th><th>已删</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in batches" :key="b.id">
            <td class="mono">{{ b.batch_no }}</td>
            <td>{{ b.stream_title }}</td>
            <td class="mono">{{ b.kind }}</td>
            <td><span class="tag">{{ b.apply_order_label }}</span></td>
            <td>
              <span class="tag" :class="b.status === 'completed' ? 'ok' : 'danger'">{{ b.status }}</span>
            </td>
            <td>{{ b.source_events }}</td>
            <td>{{ b.applied_upsert }}</td>
            <td>{{ b.applied_delete }}</td>
            <td>{{ b.held_events }}</td>
            <td>{{ b.projected_alive }}</td>
            <td>{{ b.projected_deleted }}</td>
            <td class="mono">{{ b.created_at }}</td>
          </tr>
          <tr v-if="!batches.length"><td colspan="12" class="muted">还没有批次，先跑一趟。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
