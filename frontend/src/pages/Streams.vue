<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const rows = ref([])
const switches = ref([])
const err = ref('')
const msg = ref('')
const busy = ref(false)

async function refresh() {
  rows.value = await getJSON('/api/streams')
  switches.value = await getJSON('/api/policy-switches?limit=20')
}

async function switchTo(s, order) {
  err.value = ''
  msg.value = ''
  busy.value = true
  try {
    const r = await postJSON(`/api/streams/${s.id}/apply-order`, { apply_order: order })
    const parts = [`${s.title}：${r.from_order_label} → ${r.to_order_label}`]
    if (r.buffered_settled) parts.push(`旧策略清算缓冲 ${r.buffered_settled} 条`)
    if (r.running_failed) parts.push(`失败未完成批次 ${r.running_failed} 个`)
    if (r.drain_batch_id) parts.push(`清算批次 #${r.drain_batch_id}`)
    if (r.rebuild_batch_id) parts.push(`重建批次 #${r.rebuild_batch_id}`)
    msg.value = parts.join(' · ')
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
    <h1>投影流</h1>
    <p class="muted">
      应用序互斥：<span class="mono">按夹具到达顺序依次投影</span> 与
      <span class="mono">按事件 seq 升序投影</span> 同时只能启用一种。
      切换时先按旧策略清算缓冲 / 失败未完成批次并单独留痕，再按新策略重建终态。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="msg" class="tag ok">{{ msg }}</p>

    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>ID</th><th>编码</th><th>标题</th><th>业务域</th>
            <th>实体键</th><th>载荷字段</th><th>夹具</th><th>水位 seq</th>
            <th>应用序</th><th>缓冲中</th><th>启用</th><th>切换</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in rows" :key="s.id">
            <td>{{ s.id }}</td>
            <td class="mono">{{ s.code }}</td>
            <td>{{ s.title }}</td>
            <td>{{ s.domain }}</td>
            <td class="mono">{{ s.entity_key_name }}</td>
            <td class="mono">{{ s.payload_fields.join(', ') }}</td>
            <td class="mono">{{ s.fixture }}</td>
            <td class="mono">{{ s.watermark_seq }}</td>
            <td><span class="tag">{{ s.apply_order_label }}</span></td>
            <td>
              <span class="tag" :class="s.open_buffer_count ? 'warn' : ''">
                {{ s.open_buffer_count }}
              </span>
            </td>
            <td><span class="tag" :class="s.enabled ? 'ok' : 'danger'">{{ s.enabled ? '是' : '否' }}</span></td>
            <td style="white-space:nowrap">
              <button
                :disabled="busy || s.apply_order === 'by_seq'"
                style="padding:0.3rem 0.55rem;font-size:0.8rem"
                @click="switchTo(s, 'by_seq')"
              >切到 seq 序</button>
              <button
                :disabled="busy || s.apply_order === 'by_arrival'"
                style="padding:0.3rem 0.55rem;font-size:0.8rem;margin-left:0.3rem"
                @click="switchTo(s, 'by_arrival')"
              >切到到达序</button>
              <router-link
                :to="{ path: '/compare', query: { stream_id: s.id } }"
                style="margin-left:0.5rem;font-size:0.82rem"
              >对照</router-link>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">策略切换留痕</h3>
      <table>
        <thead>
          <tr>
            <th>#</th><th>流</th><th>切换</th><th>清空缓冲</th><th>失败批次</th>
            <th>清算批次</th><th>重建批次</th><th>备注</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in switches" :key="p.id">
            <td class="mono">{{ p.id }}</td>
            <td>{{ p.stream_title }}</td>
            <td class="mono">{{ p.from_order }} → {{ p.to_order }}</td>
            <td>{{ p.buffered_settled ?? p.buffered_failed }}</td>
            <td>{{ p.running_failed }}</td>
            <td class="mono">{{ p.drain_batch_id ?? '—' }}</td>
            <td class="mono">{{ p.rebuild_batch_id ?? '—' }}</td>
            <td class="mono">{{ p.note || '—' }}</td>
            <td class="mono">{{ p.created_at }}</td>
          </tr>
          <tr v-if="!switches.length"><td colspan="9" class="muted">尚无策略切换。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
