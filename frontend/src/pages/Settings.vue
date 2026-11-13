<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const settings = ref({})
const streams = ref([])
const switches = ref([])
const err = ref('')
onMounted(async () => {
  try {
    settings.value = await getJSON('/api/settings')
    streams.value = await getJSON('/api/streams')
    switches.value = await getJSON('/api/policy-switches?limit=10')
  } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>设置</h1>
    <p class="muted">
      应用序是<strong>按流互斥</strong>的配置，切换入口在「投影流」与「应用序对照」页。
      存在未清算缓冲或进行中批次时，切换会先按旧策略清算/失败掉再切换，过程单独留痕。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <h3 style="margin-top:0">各流当前应用序</h3>
      <table>
        <thead><tr><th>投影流</th><th>应用序</th><th>缓冲中</th><th>切换</th></tr></thead>
        <tbody>
          <tr v-for="s in streams" :key="s.id">
            <td>{{ s.title }} <span class="mono muted">({{ s.code }})</span></td>
            <td><span class="tag">{{ s.apply_order_label }}</span></td>
            <td><span class="tag" :class="s.open_buffer_count ? 'warn' : ''">{{ s.open_buffer_count }}</span></td>
            <td><router-link :to="{ path: '/compare', query: { stream_id: s.id } }">去对照页切换</router-link></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">其余底座设置</h3>
      <table>
        <thead><tr><th>键</th><th>值</th></tr></thead>
        <tbody>
          <tr v-for="(v, k) in settings" :key="k">
            <td class="mono">{{ k }}</td>
            <td><span class="tag">{{ v }}</span></td>
          </tr>
        </tbody>
      </table>
      <p class="muted" style="margin-bottom:0">
        <span class="mono">apply_order</span> 仅作全局默认参考；实际投影以每条流自己的应用序为准。
      </p>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">最近策略切换</h3>
      <table>
        <thead><tr><th>#</th><th>流</th><th>切换</th><th>清算缓冲</th><th>失败批次</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="p in switches" :key="p.id">
            <td class="mono">{{ p.id }}</td>
            <td>{{ p.stream_title }}</td>
            <td class="mono">{{ p.from_order }} → {{ p.to_order }}</td>
            <td>{{ p.buffered_settled }}</td>
            <td>{{ p.running_failed }}</td>
            <td class="mono">{{ p.created_at }}</td>
          </tr>
          <tr v-if="!switches.length"><td colspan="6" class="muted">尚无策略切换。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
