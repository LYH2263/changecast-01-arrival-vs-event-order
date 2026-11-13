<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const stats = ref(null)
const err = ref('')
onMounted(async () => {
  try { stats.value = await getJSON('/api/dashboard') } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>总览</h1>
    <p class="muted">变更日志投影：源 CDC 事件按序落到当前投影，批次计数与快照互证。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" v-if="stats">
      <div class="stat-grid">
        <div class="stat"><div class="stat-num">{{ stats.stream_count }}</div><div class="stat-label">投影流</div></div>
        <div class="stat"><div class="stat-num">{{ stats.enabled_streams }}</div><div class="stat-label">已启用</div></div>
        <div class="stat"><div class="stat-num">{{ stats.batch_count }}</div><div class="stat-label">应用批次</div></div>
        <div class="stat"><div class="stat-num">{{ stats.projection_alive }}</div><div class="stat-label">存活投影</div></div>
        <div class="stat"><div class="stat-num">{{ stats.projection_deleted }}</div><div class="stat-label">已删投影</div></div>
        <div class="stat"><div class="stat-num">{{ stats.ooo_buffered }}</div><div class="stat-label">缓冲中</div></div>
      </div>
      <p style="margin-top:1rem">
        全局默认应用序 <span class="tag">{{ stats.apply_order_labels?.[stats.apply_order] || stats.apply_order }}</span>
        （每条流各自互斥选择，实际以流配置为准）
      </p>
      <table>
        <thead>
          <tr>
            <th>投影流</th><th>应用序</th><th>缓冲中</th><th>最近批次</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in stats.streams" :key="s.id">
            <td>{{ s.title }}</td>
            <td><span class="tag">{{ s.apply_order === 'by_arrival' ? '到达序' : 'seq 序' }}</span></td>
            <td>
              <span class="tag" :class="s.open_buffer_count ? 'warn' : ''">{{ s.open_buffer_count }}</span>
            </td>
            <td class="mono" style="font-size:0.82rem">
              {{ s.last_batch ? `${s.last_batch.batch_no} · ${s.last_batch.apply_order}` : '—' }}
            </td>
            <td>
              <router-link :to="{ path: '/compare', query: { stream_id: s.id } }">对照</router-link>
            </td>
          </tr>
        </tbody>
      </table>
      <p style="margin-top:1rem">
        <router-link to="/run">去投影台跑一趟 →</router-link>
        ·
        <router-link to="/verify">同源核验 →</router-link>
      </p>
    </div>
  </div>
</template>
