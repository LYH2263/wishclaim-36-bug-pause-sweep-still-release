<template>
  <div class="wall">
    <h1 class="serif">愿望墙</h1>
    <p class="tag">无顶栏 · 瀑布流 · 点卡片认领</p>
    <div class="masonry">
      <article v-for="w in rows" :key="w.id" class="card" @click="$router.push('/wishes/'+w.id)">
        <h3>{{ w.title || '（无标题）' }}</h3>
        <p>{{ w.note }}</p>
        <span class="tag">{{ w.status }} · {{ w.data_quality }}</span>
        <span v-if="w.remaining_seconds != null" class="tag">
          {{ w.paused ? '已暂停 · 剩余 ' + w.remaining_seconds + 's' : '剩余 ' + w.remaining_seconds + 's' }}
        </span>
      </article>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '../api'
const rows = ref([])
let timer
onMounted(async () => {
  rows.value = await api('/wishes')
  // 本地续跑倒计时; paused 行冻结不动, 与详情/规则同口径
  timer = setInterval(() => {
    for (const w of rows.value) {
      if (!w.paused && w.remaining_seconds > 0) w.remaining_seconds--
    }
  }, 1000)
})
onUnmounted(() => clearInterval(timer))
</script>
