// 앱 시작점: Vue 앱을 만들고 상태 저장소(Pinia), 라우터(화면 이동), 모션 CSS와 전역 지시자를 붙여 #app에 띄운다.
//   v-reveal: 화면에 들어올 때 나타나는 효과, v-glider: 탭 밑줄이 선택된 탭으로 미끄러지는 효과 (motion/directives.js)
import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import './motion/motion.css'
import { glider, reveal } from './motion/directives'

createApp(App).use(createPinia()).use(router).directive('reveal', reveal).directive('glider', glider).mount('#app')
