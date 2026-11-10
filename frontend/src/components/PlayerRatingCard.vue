<script setup>
// 선수 능력치 카드(포지션별 능력치). 선수 화면 맨 위.
// 위쪽 띠: 통산과 모든 시즌을 늘어놓고, 각각의 종합 점수를 작은 막대로 보여 준다(그래서 띠 자체가 시즌별 추이가 된다).
// 하나를 고르면 그 카드: 종합 게이지, 순위, 능력치마다 불릿 막대(포지션 평균 60에 세로선), 근거 기록,
// 같은 포지션 직전 시즌 대비 변화. 통산 = 시즌 점수를 출전 세트로 가중 평균(spark_jobs/player_ratings.py career_cards).
// 차트 지침(ui-ux-pro-max "불릿 차트"): 숫자와 기준선을 글자로 적고, 색은 그것을 반복할 뿐이다.
// 상호작용: 띠는 탭 목록(←→, Home/End로 이동). 능력치를 가리키거나 포커스하면 무엇을 쟀는지 설명이 나온다.
// 모션: 카드를 바꾸면 막대·게이지·숫자가 새 값으로 미끄러지고, 띠는 고른 항목이 보이게 스크롤된다.
import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import ScoreRing from '@/components/ScoreRing.vue'
import { ABILITY_HELP, bandOf, formatStat, yearsLong, yearsShort } from '@/constants/ratings'
import AnimatedNumber from '@/motion/AnimatedNumber.vue'
import { reducedMotion, useInView } from '@/motion/core'

const props = defineProps({
  ratings: { type: Object, required: true }, // /api/players/{code}의 `ratings`: {seasons(오래된 순), careers}
  season: { type: String, default: '' }, // 화면의 시즌: 그 시즌 카드가 있으면 그것을 먼저 보여 준다
  gender: { type: String, required: true },
  preferCareer: Boolean, // 역대 순위표에서 들어온 경우: 통산 카드부터
})

// 두 번째 통산 카드(다른 포지션)는 그 포지션으로 2시즌 이상 뛰었을 때만(1시즌뿐이면 시즌 카드와 같은 내용이라)
const careers = computed(() => props.ratings.careers.filter((c, i) => i === 0 || c.seasons >= 2))
const seasons = computed(() => props.ratings.seasons)
// 주 포지션(통산 세트가 가장 많은 포지션). 다른 포지션 시즌이 섞여 있으면 띠에 포지션 코드도 적는다
const mainPosition = computed(() => props.ratings.careers[0]?.position ?? seasons.value.at(-1)?.position)
const showPosition = computed(() => careers.value.length > 1 || seasons.value.some((s) => s.position !== mainPosition.value))
// 띠 항목의 키: 시즌 카드는 시즌 코드, 통산 카드는 'career-포지션'
const keyOf = (item) => (item.season ? item.season.code : `career-${item.position}`)
const items = computed(() => [...careers.value, ...seasons.value])

const pick = ref('') // 고른 항목의 키
// 처음 고를 항목: 화면 시즌의 카드 → 없으면 통산 → 없으면 가장 최근 시즌
function defaultPick() {
  const s = props.preferCareer ? null : seasons.value.find((x) => x.season.code === props.season)
  return keyOf(s || careers.value[0] || seasons.value.at(-1))
}
pick.value = defaultPick()
// 화면의 시즌이 바뀌거나 데이터를 다시 받으면 기본 항목으로 돌아간다
watch(() => [props.ratings, props.season], () => { pick.value = defaultPick(); nextTick(() => keepInView(false)) })

// 지금 보여 줄 카드와, 비교할 직전 시즌 카드(통산이면 없음)
const card = computed(() => items.value.find((i) => keyOf(i) === pick.value) || items.value[0])
const isCareer = computed(() => !card.value.season)
const prev = computed(() => (isCareer.value ? null : card.value.previous))
// 변화량(어느 한쪽이 없으면 null), 화살표 기호, 색 클래스
const delta = (now, before) => (now == null || before == null ? null : now - before)
const overallDelta = computed(() => delta(card.value.overall, prev.value?.overall))
const arrow = (d) => (d > 0 ? '▲' : d < 0 ? '▼' : '–')
const trend = (d) => (d > 0 ? 'up' : d < 0 ? 'down' : '')

// 띠의 작은 막대 높이: 30~99점을 막대 높이에 맞춘다(최소 6%). 점선은 포지션 평균(60)
const fill = (v) => (v == null ? 0 : Math.max(0.06, Math.min(1, (v - 30) / 69)))
// 띠 항목마다 화면 낭독기가 읽을 설명
const tabLabel = (item) => `${item.season ? `${yearsLong(item.season.years)} 시즌` : '통산'} ${item.position_name}`
  + ` 종합 ${item.overall ?? '없음'}${item.season && !item.qualified ? ' (잠정)' : ''}`

const id = useId()
const strip = ref(null)
// 고른 항목이 띠의 보이는 영역 가운데에 오도록 가로 스크롤(smooth=false면 즉시)
function keepInView(smooth = true) {
  const s = strip.value
  const btn = s?.querySelector(`[data-key="${pick.value}"]`)
  if (!btn) return
  if (btn.closest('.careers')) { // 통산은 왼쪽에 고정돼 있다. 처음 보여 줄 때는 최근 시즌 쪽(오른쪽 끝)부터
    if (!smooth) s.scrollLeft = s.scrollWidth
    return
  }
  const pinned = s.querySelector('.careers')?.offsetWidth || 0 // 고정된 통산 부분이 왼쪽을 가리는 폭
  const left = btn.offsetLeft + btn.offsetWidth / 2 - (pinned + s.clientWidth) / 2
  s.scrollTo({ left: Math.max(0, left), behavior: smooth && !reducedMotion.value ? 'smooth' : 'auto' })
}
// 항목 고르기(focus면 그 탭으로 키보드 포커스도 옮김)
async function select(key, focus = false) {
  pick.value = key
  await nextTick()
  if (focus) strip.value?.querySelector(`[data-key="${key}"]`)?.focus({ preventScroll: true })
  keepInView()
}
// 탭 목록 키보드: ←→ 이웃 항목, Home/End 처음·끝
function onKey(e) {
  const keys = items.value.map(keyOf)
  const i = keys.indexOf(pick.value)
  const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: keys.length - 1 }[e.key]
  if (next == null) return
  e.preventDefault()
  select(keys[Math.max(0, Math.min(keys.length - 1, next))], true)
}
onMounted(() => keepInView(false))

const el = ref(null)
const seen = useInView(el)
const focus = ref(null) // 가리키거나 포커스한 능력치 키(설명 표시용)
const focused = computed(() => card.value.abilities.find((a) => a.key === focus.value))
// 화면 낭독기용 카드 요약 한 문장
const summary = computed(() => {
  const c = card.value
  const parts = c.abilities.filter((a) => a.score != null).map((a) => `${a.label} ${a.score}`).join(', ')
  return `${isCareer.value ? '통산' : `${yearsLong(c.season.years)} 시즌`} ${c.position_name} 종합 ${c.overall ?? '없음'}점. ${parts}.`
})
</script>

<template>
  <div ref="el" class="rc">
    <div class="strip-head small muted">
      <span>시즌을 고르면 그 시즌 카드를 보여 줘요</span>
      <span class="legend" aria-hidden="true"><i /> 포지션 평균 60</span>
    </div>
    <div ref="strip" class="strip" role="tablist" aria-label="능력치 카드: 통산과 시즌" @keydown="onKey">
      <div class="careers" role="none">
        <button v-for="c in careers" :id="`${id}-${keyOf(c)}`" :key="keyOf(c)" type="button" role="tab" class="it career"
                :data-key="keyOf(c)" :aria-selected="pick === keyOf(c)" :aria-controls="`${id}-panel`"
                :tabindex="pick === keyOf(c) ? 0 : -1" :aria-label="tabLabel(c)" @click="select(keyOf(c))">
          <span class="v">{{ c.overall ?? '-' }}</span>
          <span class="col" aria-hidden="true"><i :class="bandOf(c.overall)" :style="{ transform: `scaleY(${seen ? fill(c.overall) : 0})` }" /></span>
          <span class="y">통산</span>
          <span v-if="showPosition" class="p">{{ c.position }}</span>
        </button>
      </div>
      <button v-for="(s, i) in seasons" :id="`${id}-${keyOf(s)}`" :key="keyOf(s)" type="button" role="tab" class="it"
              :class="{ light: !s.qualified }" :data-key="keyOf(s)" :aria-selected="pick === keyOf(s)"
              :aria-controls="`${id}-panel`" :tabindex="pick === keyOf(s) ? 0 : -1" :aria-label="tabLabel(s)"
              :title="`${yearsLong(s.season.years)} · ${s.team.name}${s.qualified ? '' : ' · 잠정'}`" @click="select(keyOf(s))">
        <span class="v">{{ s.overall ?? '-' }}</span>
        <span class="col" aria-hidden="true">
          <i :class="bandOf(s.overall)" :style="{ transform: `scaleY(${seen ? fill(s.overall) : 0})`, transitionDelay: `${i * 30}ms` }" />
        </span>
        <span class="y">{{ yearsShort(s.season.years) }}</span>
        <span v-if="showPosition" class="p">{{ s.position }}</span>
      </button>
    </div>

    <div :id="`${id}-panel`" role="tabpanel" :aria-labelledby="`${id}-${keyOf(card)}`">
      <p class="sr-only">{{ summary }}</p>
      <div class="head">
        <ScoreRing :value="card.overall" :size="104" aria-hidden="true" />
        <div class="who">
          <span class="pos">{{ card.position_name }} · {{ isCareer ? '통산' : `${yearsLong(card.season.years)} 정규리그` }}</span>
          <b v-if="card.rank" class="rank">{{ isCareer ? '역대 ' : '' }}{{ card.peers }}명 중 {{ card.rank }}위</b>
          <span v-else-if="isCareer" class="unranked">역대 순위는 순위 자격 시즌 3번부터 (지금 {{ card.qualified_seasons }}번)</span>
          <span v-else class="unranked">순위 자격(팀 세트의 1/3) 미달</span>
          <span class="small muted">
            <template v-if="isCareer">{{ yearsLong(card.first.years) }}<template v-if="card.last.code !== card.first.code">
              ~ {{ yearsLong(card.last.years) }}</template> · {{ card.seasons }}시즌 · {{ card.sets.toLocaleString() }}세트</template>
            <template v-else>{{ card.team.name }} · {{ card.sets }}세트</template>
          </span>
          <span v-if="!isCareer && card.provisional" class="prov small">잠정 · 출전이 적어 평균 쪽으로 당긴 점수</span>
        </div>
        <div v-if="overallDelta != null" class="change small">
          <span class="muted">{{ yearsLong(prev.season.years) }} 대비</span>
          <b :class="trend(overallDelta)">{{ arrow(overallDelta) }} {{ Math.abs(overallDelta) }}</b>
        </div>
        <button v-else-if="isCareer && card.best && card.seasons > 1" type="button" class="change best small"
                @click="select(card.best.season.code, true)">
          <span class="muted">최고 시즌</span>
          <b>{{ yearsLong(card.best.season.years) }} · {{ card.best.overall }}</b>
        </button>
      </div>

      <Transition name="swap" mode="out-in">
        <ul :key="card.position" class="abilities" @pointerleave="focus = null">
          <li v-for="(a, i) in card.abilities" :key="a.key" tabindex="0" :class="{ on: focus === a.key, ref: !a.weight }"
              @pointerenter="focus = a.key" @focus="focus = a.key" @blur="focus = null">
            <span class="label">{{ a.label }}<span class="w">{{ a.weight ? `${a.weight}%` : '참고' }}</span></span>
            <span class="track" aria-hidden="true">
              <i :class="bandOf(a.score)" :style="{ transform: `scaleX(${seen && a.score != null ? a.score / 99 : 0})`,
                                                    transitionDelay: `${i * 60}ms` }" />
              <span class="avg" />
            </span>
            <span class="score num"><AnimatedNumber :value="a.score" count-up /></span>
            <span class="d small num" :class="trend(delta(a.score, prev?.abilities[a.key]))">
              <template v-if="delta(a.score, prev?.abilities[a.key]) != null">
                {{ arrow(delta(a.score, prev.abilities[a.key])) }}{{ Math.abs(delta(a.score, prev.abilities[a.key])) || '' }}
              </template>
            </span>
            <span class="stat small muted">{{ a.stats.map(formatStat).join(' · ') || '기록 부족' }}<template v-if="a.note"> · {{ a.note }}</template></span>
          </li>
        </ul>
      </Transition>

      <p class="explain small" aria-live="polite">
        <template v-if="focused"><b>{{ focused.label }}</b> · {{ ABILITY_HELP[focused.key] }}</template>
        <template v-else>막대의 세로선이 같은 포지션 주전 평균(60)이에요. 72면 상위 약 16%, 84면 상위 약 2%예요.
          항목에 마우스를 올리면 무엇을 쟀는지 알려 드려요.</template>
      </p>
      <p v-if="card.extra?.set_success_ps != null" class="small muted extra">
        KOVO 기록 세트당 세트 성공 {{ card.extra.set_success_ps.toFixed(2) }}{{ isCareer ? ' (통산)' : '' }}
        (토스가 득점으로 이어진 횟수라 공격수 실력이 크게 섞여요)
      </p>
      <p class="small muted foot">
        <template v-if="isCareer">통산 능력치 = 시즌별 점수를 출전 세트 수로 가중 평균한 값이에요. 시즌마다 그 시기 선수들과 비교한
          점수라 시대가 달라도 같은 눈금이고, 근거 기록은 시즌 기록을 모두 합해 계산해요. 역대 순위는 순위 자격 시즌이
          3번 이상인 같은 성별·포지션 선수끼리 비교해요.</template>
        <template v-else>종합 = 능력치 점수의 가중 평균(괄호 안 % 비중). 같은 성별·포지션의 순위 자격 선수(그 시즌과 앞 두
          시즌)와 비교했고, 기록이 적으면 평균 쪽으로 당겨 계산해요.</template>
        <RouterLink :to="{ path: '/ratings', query: { position: card.position, gender, scope: isCareer ? 'career' : undefined } }">
          {{ isCareer ? '역대 순위 보기' : '포지션별 순위 보기' }} →</RouterLink>
      </p>
    </div>
  </div>
</template>

<style scoped>
/* season strip */
.strip-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
.legend { display: inline-flex; align-items: center; gap: 6px; }
.legend i { width: 16px; height: 0; border-top: 1px dashed var(--color-muted-foreground); }
.strip { position: relative; display: flex; gap: 2px; overflow-x: auto; overscroll-behavior-x: contain; scrollbar-width: thin;
         padding: 2px 2px 6px 0; margin: 0 0 var(--space-4); }
.careers { position: sticky; left: 0; z-index: 1; display: flex; gap: 2px; flex: none; background: var(--color-card);
           padding: 0 6px 0 2px; margin-right: 4px; border-right: 1px solid var(--color-border); }
.it { flex: none; width: 46px; display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 6px 2px 5px;
      border: 1px solid transparent; border-radius: 8px; background: none; color: var(--color-muted-foreground);
      cursor: pointer; font: inherit; transition: background-color var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out); }
.it:hover { background: var(--color-muted); }
.it[aria-selected="true"] { background: var(--color-primary-soft); border-color: var(--color-primary); color: var(--color-heading); }
.it:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 1px; }
.it .v { font-weight: 800; font-size: .9rem; line-height: 1.1; color: var(--color-heading); font-variant-numeric: tabular-nums; }
.it.light .v { color: var(--color-muted-foreground); font-weight: 600; }
.col { position: relative; width: 100%; height: 40px;
       /* dashed position average at 60: (60 - 30) / 69 of the height from the bottom */
       background: repeating-linear-gradient(90deg, var(--color-border-strong) 0 3px, transparent 3px 6px) 0 56.5% / 100% 1px no-repeat; }
.col i { position: absolute; bottom: 0; left: 50%; width: 14px; height: 100%; margin-left: -7px; border-radius: 3px 3px 0 0;
         transform-origin: center bottom; transition: transform var(--dur-chart) var(--ease-out); }
.it.light .col i { opacity: .4; }
/* score bands (bandOf) for both the strip columns and the ability bars; the number is always written beside */
:is(.col, .track) i.top { background: var(--color-heading); }
:is(.col, .track) i.high { background: var(--color-primary); }
:is(.col, .track) i.mid { background: color-mix(in srgb, var(--color-primary) 55%, white); }
:is(.col, .track) i:is(.low, .none) { background: var(--color-muted-foreground); opacity: .55; }
.it .y { font-size: .72rem; line-height: 1.1; white-space: nowrap; }
.it[aria-selected="true"] .y { font-weight: 800; }
.it.career .y { font-weight: 800; color: var(--color-primary); }
.it .p { font-size: .65rem; line-height: 1; opacity: .8; }

/* the card */
.head { display: flex; align-items: center; gap: var(--space-4); flex-wrap: wrap; margin-bottom: var(--space-3); }
.who { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1 1 190px; } /* wraps the side box below on phones */
.pos { font-size: .8rem; font-weight: 700; color: var(--color-primary); }
.rank { font-size: 1.35rem; color: var(--color-heading); }
.unranked { font-weight: 700; color: var(--color-muted-foreground); }
.prov { align-self: flex-start; background: var(--color-accent-soft); color: var(--color-accent); border-radius: 999px; padding: 0 8px; font-weight: 700; }
.change { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.change b { font-size: 1.05rem; }
.best { border: 1px solid var(--color-border); background: var(--color-card); border-radius: 8px; padding: 6px 10px; cursor: pointer;
        font: inherit; color: var(--color-heading); min-height: 44px; transition: border-color var(--dur-fast) var(--ease-out); }
.best:hover { border-color: var(--color-primary); }
.up { color: var(--color-good); }
.down { color: var(--color-bad); }

.abilities { list-style: none; margin: 0; padding: 0; }
.abilities li { display: grid; grid-template-columns: 112px 1fr 40px 36px; grid-template-areas: "label track score d" "stat stat stat stat";
                column-gap: 10px; row-gap: 2px; align-items: center; padding: 7px 6px; margin: 0 -6px; border-radius: 8px;
                border-bottom: 1px solid var(--color-border-soft); outline-offset: -2px; cursor: default;
                transition: background-color var(--dur-fast) var(--ease-out); }
.abilities li.on { background: var(--color-primary-soft); }
.label { grid-area: label; font-weight: 700; display: flex; align-items: baseline; gap: 6px; }
.w { font-size: .72rem; font-weight: 500; color: var(--color-muted-foreground); }
.ref .label { color: var(--color-muted-foreground); }
.track { grid-area: track; position: relative; height: 10px; background: var(--color-muted); border-radius: 5px; overflow: hidden; }
.track i { position: absolute; inset: 0; transform-origin: left center; border-radius: 5px;
           transition: transform var(--dur-chart) var(--ease-out); }
.ref .track i { opacity: .45; }
/* the position average (60 of 99) as a bullet-chart marker */
.avg { position: absolute; top: -2px; bottom: -2px; left: calc(60 / 99 * 100%); width: 2px; background: var(--color-foreground); opacity: .55; }
.score { grid-area: score; text-align: right; font-weight: 800; font-size: 1.05rem; color: var(--color-heading); }
.d { grid-area: d; text-align: left; font-weight: 700; }
.stat { grid-area: stat; }
.explain { min-height: 2.6em; margin: var(--space-3) 0 0; padding: 8px 10px; background: var(--color-muted); border-radius: 8px; }
.extra { margin: var(--space-2) 0 0; }
.foot { margin: var(--space-2) 0 0; }
@media (max-width: 480px) { .abilities li { grid-template-columns: 84px 1fr 34px 30px; } }
</style>
