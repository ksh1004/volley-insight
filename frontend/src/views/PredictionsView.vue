<script setup>
// ANL-10: how good the win predictions are. Backtest (each season predicted by a model trained only on earlier
// seasons) against Elo, calibration (sure predictions should be right more often), this season's real results.
// Chart fallback (no calibration chart in the design data): paired bars with the numbers and counts written out.
// Motion: the headline numbers count up and glide when the division changes (old values stay, dimmed, until the
// new ones arrive); calibration bars fill when first seen.
import { computed, onMounted, ref, watch } from 'vue'
import { getPredictionCheck } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import CompareBars from '@/components/CompareBars.vue'
import { GENDERS } from '@/constants/volley'
import AnimatedNumber from '@/motion/AnimatedNumber.vue'
import { useInView } from '@/motion/core'
import { fmtDate } from '@/utils/format'

const gender = ref('')
const data = ref(null)
const state = ref({ loading: true, error: null })

async function load() {
  state.value.loading = true
  try {
    data.value = await getPredictionCheck(gender.value || undefined)
    state.value.error = null
  } catch (e) {
    state.value.error = e.message
  } finally {
    state.value.loading = false
  }
}
onMounted(load)
watch(gender, load)

const pct = (v) => (v == null ? '-' : `${(v * 100).toFixed(1)}%`)
const ll = (v) => (v == null ? '-' : v.toFixed(3))
const bt = computed(() => data.value?.backtest)
const live = computed(() => data.value?.live)
const seasonRows = computed(() => (bt.value?.seasons || []).map((s) => ({
  label: s.season.years.replace('-', '–'), a: s.model.accuracy, b: s.elo.accuracy, fmt: pct,
})))
const gain = computed(() => {
  const o = bt.value?.overall
  return o ? (o.model.accuracy - o.elo.accuracy) * 100 : null
})
const pctN = (v) => `${v.toFixed(1)}%`
const llN = (v) => v.toFixed(3)
const calibEl = ref(null)
const calibSeen = useInView(calibEl)
const winner = (r) => (r.home_win_prob >= 0.5 ? r.home.name : r.away.name)
const sure = (r) => Math.round(Math.max(r.home_win_prob, 1 - r.home_win_prob) * 100)
</script>

<template>
  <div class="title-row">
    <div>
      <h1>예측 검증</h1>
      <p class="muted small">경기 전 승리 확률 예측이 얼마나 맞았는지 숫자로 공개합니다.</p>
    </div>
    <div v-glider class="tabs" role="group" aria-label="남녀부">
      <button type="button" class="tab" :aria-pressed="gender === ''" @click="gender = ''">전체</button>
      <button v-for="(name, g) in GENDERS" :key="g" type="button" class="tab" :aria-pressed="gender === g" @click="gender = g">{{ name }}</button>
    </div>
  </div>

  <AsyncState :loading="state.loading && !data" :error="state.error">
    <div v-if="data" :class="{ busy: state.loading }" :aria-busy="state.loading">
      <section class="card kpis">
        <div>
          <span class="muted small">백테스트 적중률 <template v-if="bt">({{ bt.seasons.length }}시즌, {{ bt.overall.model.n }}경기)</template></span>
          <b><AnimatedNumber :value="bt ? bt.overall.model.accuracy * 100 : null" :format="pctN" count-up /></b>
          <span v-if="gain != null" class="small" :class="gain >= 0 ? 'good' : 'bad'">Elo보다 {{ gain >= 0 ? '+' : '' }}{{ gain.toFixed(1) }}%p</span>
        </div>
        <div>
          <span class="muted small">Log Loss (낮을수록 좋음)</span>
          <b><AnimatedNumber :value="bt ? bt.overall.model.log_loss : null" :format="llN" /></b>
          <span v-if="bt" class="muted small">Elo {{ ll(bt.overall.elo.log_loss) }}</span>
        </div>
        <div>
          <span class="muted small">이번 시즌 실제 예측</span>
          <b><AnimatedNumber :value="live.overall.n ? live.overall.accuracy * 100 : null" :format="pctN" count-up /></b>
          <span class="muted small">{{ live.overall.n }}경기 중 {{ Math.round((live.overall.accuracy || 0) * live.overall.n) }}경기 적중</span>
        </div>
        <div v-if="data.model">
          <span class="muted small">사용 중인 모델</span>
          <b>v{{ data.model.champion_version ?? data.model.version }}</b>
          <span class="muted small">학습 {{ data.model.train_seasons }}</span>
        </div>
      </section>

      <div class="grid-2">
        <section v-reveal class="card">
          <h2>시즌별 적중률 <span class="muted small">백테스트 · 모델 vs Elo</span></h2>
          <template v-if="bt">
            <CompareBars :rows="seasonRows" a-label="예측 모델" b-label="Elo 기준" b-muted />
            <div class="table-wrap">
              <table class="data small">
                <caption class="sr-only">시즌별 백테스트 지표</caption>
                <thead><tr><th class="left">시즌</th><th>경기</th><th>적중률</th><th>Log Loss</th><th>Brier</th><th>Elo 적중률</th><th>Elo Log Loss</th></tr></thead>
                <tbody>
                  <tr v-for="s in bt.seasons" :key="s.season.code">
                    <td class="left">{{ s.season.years.replace('-', '–') }}</td><td>{{ s.model.n }}</td>
                    <td :class="{ strong: s.model.accuracy > s.elo.accuracy }">{{ pct(s.model.accuracy) }}</td>
                    <td :class="{ strong: s.model.log_loss < s.elo.log_loss }">{{ ll(s.model.log_loss) }}</td>
                    <td>{{ ll(s.model.brier) }}</td><td>{{ pct(s.elo.accuracy) }}</td><td>{{ ll(s.elo.log_loss) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="muted small note">각 시즌은 그 이전 시즌 기록만으로 학습한 모델로 예측했습니다. 예측할 시즌의 결과는 학습에 쓰지 않았습니다.</p>
          </template>
          <p v-else class="empty">아직 백테스트 결과가 없습니다. 주간 재학습(ml_weekly_retrain) 후 표시됩니다.</p>
        </section>

        <section v-reveal class="card">
          <h2>확신도별 실제 적중률 <span class="muted small">백테스트</span></h2>
          <template v-if="bt?.calibration.length">
            <ul ref="calibEl" class="calib">
              <li v-for="(c, i) in bt.calibration" :key="c.from" :style="{ '--d': `${i * 60}ms` }">
                <span class="range small">{{ Math.round(c.from * 100) }}~{{ Math.round(c.to * 100) }}%</span>
                <span class="bars" aria-hidden="true">
                  <i class="pred" :style="{ transform: `scaleX(${calibSeen ? c.predicted : 0})` }" />
                  <i class="act" :style="{ transform: `scaleX(${calibSeen ? c.actual : 0})` }" />
                </span>
                <span class="nums small">예측 <b>{{ pct(c.predicted) }}</b> · 실제 <b>{{ pct(c.actual) }}</b> <span class="muted">({{ c.n }}경기)</span></span>
              </li>
            </ul>
            <div class="legend small"><span><i class="sw pred" />모델이 말한 확률</span><span><i class="sw act" />실제로 맞힌 비율</span></div>
            <p class="muted small note">두 막대가 비슷할수록 확률을 믿을 만합니다. 예: 70%라고 한 경기는 실제로 약 70% 맞아야 합니다.</p>
          </template>
          <p v-else class="empty">아직 결과가 없습니다.</p>
        </section>
      </div>

      <section v-reveal class="card">
        <div class="card-head">
          <h2>이번 시즌 예측 결과 <span class="muted small">경기 전에 저장한 예측</span></h2>
          <span v-if="live.by_version.length > 1" class="muted small">
            <template v-for="v in live.by_version" :key="v.version">v{{ v.version }} {{ pct(v.accuracy) }} ({{ v.n }}) · </template>
          </span>
        </div>
        <ol v-if="live.recent.length" class="recent">
          <li v-for="(r, i) in live.recent" :key="r.match" v-reveal="i">
            <span class="badge" :class="r.is_correct ? 'w' : 'l'">{{ r.is_correct ? '적중' : '빗나감' }}</span>
            <RouterLink :to="`/matches/${r.match}`" class="teams">{{ r.home.name }} {{ r.sets[0] }}:{{ r.sets[1] }} {{ r.away.name }}</RouterLink>
            <span class="muted small">예측 {{ winner(r) }} 승 {{ sure(r) }}% · {{ r.pred_sets[0] }}:{{ r.pred_sets[1] }}</span>
            <span class="muted small">{{ GENDERS[r.gender] }} · {{ fmtDate(r.date) }}</span>
          </li>
        </ol>
        <p v-else class="empty">아직 끝난 경기가 없습니다.</p>
        <p v-if="live.overall.n && live.overall.n < 30" class="muted small note">경기 수가 적어 적중률이 크게 흔들릴 수 있습니다. 모델의 실력은 백테스트 결과를 함께 보세요.</p>
      </section>
    </div>
  </AsyncState>
</template>

<style scoped>
.title-row { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px; flex-wrap: wrap; margin-bottom: var(--space-3); }
.title-row h1 { margin: 0; }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-3); }
.kpis div { display: flex; flex-direction: column; gap: 2px; }
.kpis b { font-size: 1.5rem; color: var(--color-heading); font-variant-numeric: tabular-nums; }
.good { color: var(--color-good); font-weight: 600; }
.bad { color: var(--color-bad); font-weight: 600; }
.note { margin: var(--space-3) 0 0; }
.table-wrap { margin-top: var(--space-3); }
.data td, .data th { text-align: right; font-variant-numeric: tabular-nums; }
.data .left { text-align: left; }
.strong { font-weight: 700; }
.calib { list-style: none; margin: 0; padding: 0; }
.calib li { display: grid; grid-template-columns: 72px 1fr auto; gap: 10px; align-items: center; padding: 6px 0;
            border-bottom: 1px solid var(--color-border-soft); }
.range { font-variant-numeric: tabular-nums; color: var(--color-muted-foreground); }
.bars { display: flex; flex-direction: column; gap: 3px; }
.bars i { display: block; height: 7px; border-radius: 4px; transform-origin: left center;
          transition: transform var(--dur-chart) var(--ease-out) var(--d, 0ms); }
.bars .act { transition-delay: calc(var(--d, 0ms) + 120ms); }
.pred, .sw.pred { background: var(--color-muted-foreground); }
.act, .sw.act { background: var(--color-primary); }
.nums { white-space: nowrap; font-variant-numeric: tabular-nums; }
.legend { display: flex; gap: 14px; margin-top: 8px; }
.sw { display: inline-block; width: 12px; height: 7px; border-radius: 3px; margin-right: 6px; }
.recent { list-style: none; margin: 0; padding: 0; }
.recent li { display: grid; grid-template-columns: 64px minmax(160px, 1fr) auto auto; gap: 10px; align-items: baseline;
             padding: 7px 0; border-bottom: 1px solid var(--color-border-soft); }
.teams { font-weight: 600; font-variant-numeric: tabular-nums; }
@media (max-width: 760px) {
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .recent li { display: flex; flex-wrap: wrap; gap: 2px 10px; }
  .recent .teams { flex: 1 1 calc(100% - 80px); }
  .recent li > .muted { flex-basis: 100%; padding-left: 74px; }
  .calib li { grid-template-columns: 64px 1fr; }
  .calib .nums { grid-column: 1 / -1; }
}
</style>
