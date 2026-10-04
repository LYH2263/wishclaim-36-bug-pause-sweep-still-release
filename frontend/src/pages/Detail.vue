<template>
  <div class="wall">
    <h1 class="serif">{{ w.title }}</h1>
    <p>{{ w.note }}</p>
    <p class="tag">状态 {{ w.status }} · 认领人 {{ w.claimer || '—' }}</p>
    <p v-if="w.remaining_seconds != null" class="tag">
      {{ w.paused ? '已暂停 · 剩余 ' + left + 's（冻结）' : '剩余 ' + left + 's' }}
    </p>
    <p v-if="err" class="err">{{ err }}</p>
    <input v-model="claimer" placeholder="你的名字" />
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button v-if="!w.paused" @click="claim">认领锁定</button>
      <button v-if="w.status==='claimed'" class="ghost" @click="pause">暂停寻货</button>
      <button v-if="w.paused" @click="resume">继续寻货</button>
      <button class="ghost" @click="release">释放</button>
      <button class="ghost" :disabled="!!w.paused" @click="fulfill">核销完成</button>
    </div>
    <p v-if="w.paused" class="tag">暂停期间禁止转让与核销，仅当前认领人可继续寻货或释放</p>
  </div>
</template>
<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '../api'
const props = defineProps({ id: String })
const w = ref({})
const claimer = ref('访客')
const err = ref('')
const left = ref(0)
let timer
async function load() {
  w.value = await api('/wishes/' + props.id)
  left.value = w.value.remaining_seconds ?? 0
}
async function act(path) {
  err.value = ''
  try { await api('/wishes/' + props.id + path, { method: 'POST', body: JSON.stringify({ claimer: claimer.value }) }); await load() }
  catch (e) { err.value = e.message }
}
const claim = () => act('/claim')
const release = () => act('/release')
const fulfill = () => act('/fulfill')
const pause = () => act('/pause')
const resume = () => act('/resume')
onMounted(() => {
  load()
  // 剩余秒续跑: 非暂停时每秒递减; 暂停时冻结, 与墙/规则同口径
  timer = setInterval(() => { if (!w.value.paused && left.value > 0) left.value-- }, 1000)
})
onUnmounted(() => clearInterval(timer))
</script>
