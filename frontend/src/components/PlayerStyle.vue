<script setup>
// Player type (K-means group) and similar players (ANL-06, WEB-07).
// The player's most distinctive features are bars from the centre (= average of the same gender), with the
// sign and words written out so the meaning never depends on colour alone.
import { computed, ref } from 'vue'

const props = defineProps({
  style: { type: Object, required: true }, // /api/players/{code} `style`
  season: { type: String, required: true }, // season code of the page
})

const FEATURE_LABELS = {
  points_ps: '세트당 득점', attack_ps: '세트당 공격 시도', attack_rate: '공격 성공률', open_ps: '세트당 오픈 공격',
  quick_ps: '세트당 속공', back_ps: '세트당 후위 공격', ace_ps: '세트당 서브 에이스', block_ps: '세트당 블로킹',
  receive_ps: '세트당 리시브', receive_eff: '리시브 효율', dig_ps: '세트당 디그', set_ps: '세트당 세트(토스)',
  error_ps: '세트당 범실',
}
const RATES = new Set(['attack_rate', 'receive_eff'])
const fmt = (f, v) => (RATES.has(f) ? `${(v * 100).toFixed(1)}%` : v.toFixed(2))

// the six features furthest from average, largest first
const standout = computed(() => Object.entries(props.style.z)
  .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 6)
  .map(([f, z]) => ({ f, z, label: FEATURE_LABELS[f] || f, value: fmt(f, props.style.features[f]), better: f === 'error_ps' ? z < 0 : z > 0 })))
const width = (z) => `${Math.min(50, (Math.abs(z) / 3) * 50)}%`
const words = (z) => (Math.abs(z) < 0.5 ? '평균 수준' : z > 0 ? (z > 1.5 ? '매우 높음' : '높음') : (z < -1.5 ? '매우 낮음' : '낮음'))

const scope = ref('season')
const list = computed(() => (scope.value === 'season' ? props.style.similar_season : props.style.similar_all))
</script>

<template>
  <div class="ps">
    <div class="type">
      <div>
        <span class="muted small">선수 유형</span>
        <p class="label">{{ style.type.label }}</p>
        <p class="muted small">기록이 비슷한 선수끼리 묶은 {{ style.type.k }}개 유형 중 하나 · 이 유형의 선수 시즌 {{ style.type.size }}개</p>
      </div>
      <ul v-if="style.type.traits.length" class="traits" aria-label="같은 포지션의 다른 유형과 다른 점">
        <li v-for="t in style.type.traits" :key="t.feature" class="badge" :class="t.z > 0 ? 'up' : 'down'">
          {{ t.label }} {{ t.z > 0 ? '▲' : '▼' }}
        </li>
      </ul>
    </div>
    <p v-if="style.provisional" class="note small">이번 시즌 {{ style.sets }}세트만 뛰어서 유형이 바뀔 수 있습니다.</p>

    <h3 class="sub">두드러진 기록 <span class="muted small">같은 성별 선수 평균과 비교</span></h3>
    <ul class="bars">
      <li v-for="s in standout" :key="s.f">
        <span class="name small">{{ s.label }}</span>
        <span class="val small">{{ s.value }}</span>
        <span class="track" aria-hidden="true"><i :class="s.z > 0 ? 'pos' : 'neg'" :style="{ width: width(s.z) }" /></span>
        <span class="word small" :class="{ good: s.better && Math.abs(s.z) >= 0.5 }">{{ words(s.z) }}</span>
      </li>
    </ul>

    <div class="sim-head">
      <h3 class="sub">비슷한 선수</h3>
      <div class="tabs" role="group" aria-label="비교 범위">
        <button type="button" class="tab" :aria-pressed="scope === 'season'" @click="scope = 'season'">같은 시즌</button>
        <button type="button" class="tab" :aria-pressed="scope === 'all'" @click="scope = 'all'">역대</button>
      </div>
    </div>
    <ol v-if="list.length" class="sim">
      <li v-for="r in list" :key="`${r.player.code}-${r.season.code}`">
        <RouterLink :to="`/players/${r.player.code}?season=${r.season.code}`" class="who">{{ r.player.name }}</RouterLink>
        <span class="muted small">{{ r.team.name }}<template v-if="scope === 'all'"> · {{ r.season.years.replace('-', '–') }}</template></span>
        <span class="small tlabel">{{ r.type }}</span>
        <span class="score small" :aria-label="`유사도 ${r.similarity}`">유사도 <b>{{ r.similarity }}</b></span>
      </li>
    </ol>
    <p v-else class="empty small">비교할 선수가 없습니다.</p>
    <p class="muted small foot">유사도 100은 모든 선수 쌍 가운데 가장 가까운 수준이라는 뜻입니다. 세트당 기록·공격 유형·성공률 13가지로 계산합니다.</p>
  </div>
</template>

<style scoped>
.type { display: flex; justify-content: space-between; gap: var(--space-3); flex-wrap: wrap; align-items: flex-start; }
.label { font-size: 1.25rem; font-weight: 800; color: var(--color-heading); margin: 2px 0; }
.type p { margin: 0; }
.traits { list-style: none; padding: 0; margin: 0; display: flex; gap: 6px; flex-wrap: wrap; }
.badge.up { background: var(--color-primary-soft); color: var(--color-primary); }
.badge.down { background: var(--color-muted); color: var(--color-muted-foreground); }
.note { margin: var(--space-2) 0 0; padding: 6px 10px; background: var(--color-muted); border-radius: 8px; }
.sub { font-size: 1rem; margin: var(--space-4) 0 var(--space-2); }
.bars { list-style: none; padding: 0; margin: 0; }
.bars li { display: grid; grid-template-columns: 120px 64px 1fr 72px; gap: 8px; align-items: center; padding: 4px 0;
           border-bottom: 1px solid var(--color-border-soft); }
.val { font-variant-numeric: tabular-nums; text-align: right; font-weight: 600; }
.track { position: relative; height: 10px; background: linear-gradient(var(--color-border), var(--color-border)) center / 1px 100% no-repeat; }
.track i { position: absolute; top: 1px; height: 8px; border-radius: 4px; }
.track i.pos { left: 50%; background: var(--color-primary); }
.track i.neg { right: 50%; background: var(--color-muted-foreground); }
.word { color: var(--color-muted-foreground); }
.word.good { color: var(--color-good); font-weight: 600; }
.sim-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.sim-head .sub { margin-bottom: 0; }
.sim-head .tabs { margin: var(--space-4) 0 0; }
.sim { list-style: none; padding: 0; margin: var(--space-2) 0 0; }
.sim li { display: grid; grid-template-columns: minmax(72px, auto) 1fr auto auto; gap: 10px; align-items: baseline; padding: 7px 2px;
          border-bottom: 1px solid var(--color-border-soft); }
.who { font-weight: 700; }
.tlabel { color: var(--color-muted-foreground); }
.score { font-variant-numeric: tabular-nums; white-space: nowrap; }
.foot { margin: var(--space-2) 0 0; }
@media (max-width: 560px) {
  .bars li { grid-template-columns: 1fr 56px 72px; }
  .bars .track { grid-column: 1 / -1; grid-row: 2; }
  .sim li { grid-template-columns: 1fr auto; grid-template-areas: "who score" "team type"; row-gap: 2px; }
  .sim .who { grid-area: who; }
  .sim .score { grid-area: score; }
  .sim .tlabel { grid-area: type; text-align: right; }
  .sim li > .muted { grid-area: team; }
}
</style>
