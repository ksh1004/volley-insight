import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import './motion/motion.css'
import { glider, reveal } from './motion/directives'

createApp(App).use(createPinia()).use(router).directive('reveal', reveal).directive('glider', glider).mount('#app')
