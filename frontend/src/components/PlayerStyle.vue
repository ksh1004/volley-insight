<script setup>
// 선수 유형(K-means 그룹)과 비슷한 선수(ANL-06, WEB-07). 선수 화면의 '선수 유형과 비슷한 선수' 카드.
// 선수의 가장 두드러진 특징을 가운데(= 같은 성별 평균)에서 뻗는 막대로 보여 주고, 부호와 말('높음' 등)을 함께 적어
// 색에만 의존하지 않게 한다.
// 모션: 처음 보일 때 막대가 가운데 선에서 자라나고, '같은 시즌 / 역대'를 바꾸면 새 목록이 미끄러져 들어온다.
import { computed, ref } from 'vue'
import { useInView } from '@/motion/core'

const props = defineProps({
  style: { type: Object, required: true }, // /api/players/{code} `style`
  season: { type: String, required: true }, // season code of the page
})

// 특징 키 → 화면 이름(spark_jobs/player_types.py의 FEATURES와 같은 키)
const FEATURE_LABELS = {
  points_ps: '세트당 득점', attack_ps: '세트당 공격 시도', attack_rate: '공격 성공률', open_ps: '세트당 오픈 공격',
  quick_ps: '세트당 속공', back_ps: '세트당 후위 공격', ace_ps: '세트당 서브 에이스', block_ps: '세트당 블로킹',
  receive_ps: '세트당 리시브', receive_eff: '리시브 효율', dig_ps: '세트당 디그', set_ps: '세트당 세트(토스)',
  error_ps: '세트당 범실',
}
const RATES = new Set(['attack_rate', 'receive_eff']) // 비율 특징(%로 표시), 나머지는 세트당 값
// 특징 값 표시
const fmt = (f, v) => (RATES.has(f) ? `${(v * 100).toFixed(1)}%` : v.toFixed(2))

// 평균에서 가장 멀리 떨어진 특징 6개(차이가 큰 순). better: 좋은 쪽으로 다른가(범실은 낮을수록 좋음)
const standout = computed(() => Object.entries(props.style.z)
  .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 6)
  .map(([f, z]) => ({ f, z, label: FEATURE_LABELS[f] || f, value: fmt(f, props.style.features[f]), better: f === 'error_ps' ? z < 0 : z > 0 })))
const barsEl = ref(null)
const seen = useInView(barsEl)
// 막대 길이: z 3을 끝으로 보고 비례(0~1)
const grow = (z) => (seen.value ? Math.min(1, Math.abs(z) / 3) : 0)
// z를 말로: 평균 수준 / 높음 / 매우 높음 / 낮음 / 매우 낮음
const words = (z) => (Math.abs(z) < 0.5 ? '평균 수준' : z > 0 ? (z > 1.5 ? '매우 높음' : '높음') : (z < -1.5 ? '매우 낮음' : '낮음'))

const scope = ref('season') // 비슷한 선수 범위: 'season'(같은 시즌) | 'all'(역대)
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
    <ul ref="barsEl" class="bars">
      <li v-for="(s, i) in standout" :key="s.f">
        <span class="name small">{{ s.label }}</span>
        <span class="val small">{{ s.value }}</span>
        <span class="track" aria-hidden="true"><i :class="s.z > 0 ? 'pos' : 'neg'"
          :style="{ transform: `scaleX(${grow(s.z)})`, transitionDelay: `${i * 60}ms` }" /></span>
        <span class="word small" :class="{ good: s.better && Math.abs(s.z) >= 0.5 }">{{ words(s.z) }}</span>
      </li>
    </ul>

    <div class="sim-head">
      <h3 class="sub">비슷한 선수</h3>
      <div v-glider class="tabs" role="group" aria-label="비교 범위">
        <button type="button" class="tab" :aria-pressed="scope === 'season'" @click="scope = 'season'">같은 시즌</button>
        <button type="button" class="tab" :aria-pressed="scope === 'all'" @click="scope = 'all'">역대</button>
      </div>
    </div>
    <TransitionGroup v-if="list.length" tag="ol" name="item" class="sim">
      <li v-for="(r, i) in list" :key="`${scope}-${r.player.code}-${r.season.code}`" :style="{ transitionDelay: `${i * 40}ms` }">
        <RouterLink :to="`/players/${r.player.code}?season=${r.season.code}`" class="who">{{ r.player.name }}</RouterLink>
        <span class="muted small">{{ r.team.name }}<template v-if="scope === 'all'"> · {{ r.season.years.replace('-', '–') }}</template></span>
        <span class="small tlabel">{{ r.type }}</span>
        <span class="score small" :aria-label="`유사도 ${r.similarity}`">유사도 <b>{{ r.similarity }}</b></span>
      </li>
    </TransitionGroup>
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
.track i { position: absolute; top: 1px; height: 8px; width: 50%; border-radius: 4px; transition: transform var(--dur-chart) var(--ease-out); }
.track i.pos { left: 50%; background: var(--color-primary); transform-origin: left center; }
.track i.neg { right: 50%; background: var(--color-muted-foreground); transform-origin: right center; }
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
