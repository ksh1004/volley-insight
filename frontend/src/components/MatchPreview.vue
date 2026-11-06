<script setup>
// WEB-08 match preview: win probability, set score probabilities, model accuracy, head-to-head, team comparison.
// For finished matches it shows the pre-match prediction next to the result (ANL-10).
import { computed } from 'vue'
import CompareBars from '@/components/CompareBars.vue'
import WinProbBar from '@/components/WinProbBar.vue'
import { fmtDate, num, pct } from '@/utils/format'

const props = defineProps({ match: { type: Object, required: true } })
const m = computed(() => props.match)
const pv = computed(() => m.value.preview)
const pred = computed(() => pv.value?.prediction)
const done = computed(() => ['FINISHED', 'CONFIRMED'].includes(m.value.status))

const ORDER = ['3:0', '3:1', '3:2', '2:3', '1:3', '0:3']
const dist = computed(() => {
  const probs = pred.value?.set_score_probs || {}
  const max = Math.max(...ORDER.map((k) => probs[k] || 0))
  return ORDER.map((k) => ({ key: k, p: probs[k] || 0, top: (probs[k] || 0) === max, home: Number(k[0]) === 3 }))
})
const model = computed(() => pred.value?.model)
const pctText = (v) => (v == null ? '-' : `${Math.round(v * 1000) / 10}%`)

const h2h = computed(() => pv.value?.head_to_head)
const rows = computed(() => {
  const h = pv.value?.home?.season
  const a = pv.value?.away?.season
  if (!h || !a) return []
  const two = (v) => num(v, 2)
  return [
    { label: '공격 성공률', a: h.attack_success_rate, b: a.attack_success_rate, fmt: pct },
    { label: '리시브 효율', a: h.receive_efficiency, b: a.receive_efficiency, fmt: pct },
    { label: '사이드아웃율', a: h.sideout_rate, b: a.sideout_rate, fmt: pct },
    { label: '브레이크율', a: h.break_rate, b: a.break_rate, fmt: pct },
    { label: '세트당 블로킹', a: h.block_per_set, b: a.block_per_set, fmt: two },
    { label: '세트당 서브', a: h.serve_per_set, b: a.serve_per_set, fmt: two },
    { label: '세트당 범실', a: h.error_per_set, b: a.error_per_set, fmt: two, lowerIsBetter: true },
  ].filter((r) => r.a != null && r.b != null)
})
const actualWinner = computed(() => (m.value.home_sets > m.value.away_sets ? m.value.home.name : m.value.away.name))
</script>

<template>
  <section class="card preview" aria-labelledby="preview-title">
    <div class="card-head">
      <h2 id="preview-title">{{ done ? '경기 전 예측' : '경기 프리뷰' }}</h2>
      <span v-if="pred && done && pred.is_correct != null" class="badge" :class="pred.is_correct ? 'w' : 'l'">
        {{ pred.is_correct ? '예측 적중' : '예측 빗나감' }} · 실제 {{ actualWinner }} 승
      </span>
    </div>

    <template v-if="pred">
      <WinProbBar :home="m.home.name" :away="m.away.name" :prob="pred.home_win_prob" />
      <p class="lead">
        {{ '예상 세트 스코어 ' }}<b class="num">{{ pred.pred_sets[0] }}:{{ pred.pred_sets[1] }}</b>
        <span class="muted small">{{ ` (가장 가능성 높은 결과, ${pctText(dist.find((d) => d.top)?.p)})` }}</span>
      </p>

      <h3 class="sub">세트 스코어별 확률 <span class="muted small">{{ m.home.name }} 기준</span></h3>
      <ul class="dist">
        <li v-for="d in dist" :key="d.key" :class="{ top: d.top, home: d.home }">
          <span class="k num">{{ d.key }}</span>
          <span class="track"><i :style="{ width: `${Math.max(2, d.p * 100 / Math.max(...dist.map((x) => x.p)))}%` }" /></span>
          <span class="v num">{{ pctText(d.p) }}</span>
        </li>
      </ul>
      <p class="muted small legend"><span class="sw home" /> {{ m.home.name }} 승 <span class="sw away" /> {{ m.away.name }} 승</p>

      <div class="model small">
        <b>이 예측은 얼마나 믿을 만한가요?</b>
        <p>
          경기 결과 예측 모델 v{{ model.version }}은 학습에 쓰지 않은 {{ model.test_season_years || model.test_season }} 시즌 경기에서 승자를
          <b>{{ pctText(model.test_accuracy) }}</b> 맞혔어요(전력 지수만 썼을 때 {{ pctText(model.elo_test_accuracy) }}).
          <template v-if="model.live_scored"> 실제 경기 전 예측은 {{ model.live_scored }}경기 중 {{ model.live_hits }}경기 적중했어요.</template>
          열 경기 중 서너 경기는 틀린다는 뜻이고, 시즌 초반에는 기록이 적어 더 흔들려요.
        </p>
      </div>
    </template>
    <p v-else class="empty">{{ done ? '이 경기는 경기 전 예측 기록이 없어요.' : '아직 예측이 없어요. 매일 아침 7시에 갱신돼요.' }}</p>

    <template v-if="!done || pred">
      <div class="grid-2 blocks">
        <div>
          <h3 class="sub">상대 전적 <span class="muted small">통산</span></h3>
          <p v-if="!h2h?.total" class="muted small">맞대결 기록이 없어요.</p>
          <template v-else>
            <p class="h2h-total num">
              <span class="home">{{ m.home.name }} {{ h2h.wins.home }}승</span> · <span class="away">{{ h2h.wins.away }}승 {{ m.away.name }}</span>
              <span class="muted small">({{ h2h.total }}경기)</span>
            </p>
            <ol class="recent small">
              <li v-for="r in h2h.recent" :key="r.match">
                <RouterLink :to="`/matches/${r.match}`">{{ fmtDate(r.date) }}</RouterLink>
                <span class="muted">{{ r.season }}</span>
                <span class="num" :class="r.winner">{{ r.score }} {{ r.winner === 'home' ? m.home.name : m.away.name }} 승</span>
              </li>
            </ol>
          </template>
        </div>
        <div>
          <h3 class="sub">최근 흐름</h3>
          <dl class="teams small">
            <template v-for="side in ['home', 'away']" :key="side">
              <dt :class="side">{{ m[side].name }}</dt>
              <dd>
                <span class="num">전력 지수 {{ pv[side].elo != null ? Math.round(pv[side].elo) : '-' }}</span>
                <span v-if="pv[side].record" class="muted"> · {{ pv[side].record }}</span>
                <span class="form">
                  <RouterLink v-for="f in pv[side].form" :key="f.match" :to="`/matches/${f.match}`" class="badge"
                              :class="f.result.toLowerCase()" :title="f.score">{{ f.result === 'W' ? '승' : '패' }}</RouterLink>
                </span>
              </dd>
            </template>
          </dl>
        </div>
      </div>

      <template v-if="rows.length">
        <h3 class="sub">시즌 지표 비교 <span class="muted small">정규리그, 현재까지</span></h3>
        <CompareBars :rows="rows" :a-label="m.home.name" :b-label="m.away.name" />
      </template>
    </template>
  </section>
</template>

<style scoped>
.lead { margin: var(--space-3) 0; }
.sub { font-size: .95rem; margin: var(--space-4) 0 var(--space-2); }
.dist { list-style: none; padding: 0; margin: 0; display: grid; gap: 4px; }
.dist li { display: grid; grid-template-columns: 40px 1fr 52px; gap: 10px; align-items: center; }
.dist .track { height: 12px; background: var(--color-muted); border-radius: 4px; overflow: hidden; }
.dist .track i { display: block; height: 100%; border-radius: 4px; background: color-mix(in srgb, var(--color-away) 55%, white); }
.dist li.home .track i { background: color-mix(in srgb, var(--color-home) 50%, white); }
.dist li.top .track i { background: var(--color-away); }
.dist li.top.home .track i { background: var(--color-home); }
.dist li.top .k, .dist li.top .v { font-weight: 700; }
.dist .v { text-align: right; }
.legend { display: flex; align-items: center; gap: 6px; margin: 6px 0 0; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; }
.sw.home { background: var(--color-home); }
.sw.away { background: var(--color-away); margin-left: 8px; }
.model { margin-top: var(--space-4); background: var(--color-muted); border-radius: 8px; padding: 10px 12px; }
.model p { margin: 4px 0 0; color: var(--color-foreground); }
.blocks { margin-top: var(--space-2); }
.h2h-total { margin: 0 0 6px; font-weight: 600; }
.home { color: var(--color-home); }
.away { color: var(--color-away); }
.recent { list-style: none; padding: 0; margin: 0; }
.recent li { display: flex; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--color-border-soft); flex-wrap: wrap; }
.teams { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; align-items: center; }
.teams dt { font-weight: 700; }
.teams dd { margin: 0; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.form { display: inline-flex; gap: 3px; }
.form a:hover { text-decoration: none; }
</style>
