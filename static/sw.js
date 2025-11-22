// static/sw.js
self.addEventListener('install', pEvent => {
    console.log('서비스 워커 설치 완료');
});

self.addEventListener('fetch', pEvent => {
    // 여기에 오프라인 캐싱 로직 등이 들어갑니다.
    // 지금은 그냥 통과시킵니다.
});