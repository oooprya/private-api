const CACHE = "dashboard-v1"

const STATIC = [
    "/admin/",
]

self.addEventListener("install", e => {

    e.waitUntil(
        caches.open(CACHE).then(cache => cache.addAll(STATIC))
    )

})

self.addEventListener("fetch", e => {

    if (e.request.method !== "GET") return

    e.respondWith(

        caches.match(e.request).then(res => {

            return res || fetch(e.request).catch(() => {
                return caches.match("/static/pwa/offline.html")
            })

        })

    )

})