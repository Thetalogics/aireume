import { useEffect, useRef, useState } from 'react'

function drawPage(gap) {
  const canvas = document.createElement('canvas')
  canvas.width = 420
  canvas.height = 560
  const g = canvas.getContext('2d')
  g.fillStyle = gap ? '#FBF6EF' : '#FFFFFF'
  g.fillRect(0, 0, 420, 560)
  g.fillStyle = gap ? '#C4A574' : '#1F4E79'
  g.fillRect(36, 40, gap ? 170 : 110, 12)
  g.fillStyle = gap ? '#F3E6D4' : '#E4EEF6'
  for (let i = 0; i < 6; i += 1) {
    g.fillRect(36, 84 + i * 68, i % 3 === 2 ? 230 : 340, 28)
  }
  return canvas
}

const COUNT = 18

/**
 * Full-bleed background. Sheets drift in from depth.
 * The ones that fit orbit. One warm sheet stays still: the gap.
 */
export default function PaperDrift({ reduced = false, progressRef = null }) {
  const host = useRef(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (reduced) return undefined
    const el = host.current
    if (!el) return undefined
    let dead = false
    let cleanup = () => {}

    ;(async () => {
      let THREE
      try {
        THREE = await import('three')
      } catch {
        if (!dead) setFailed(true)
        return
      }
      if (dead) return
      let renderer
      try {
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' })
      } catch {
        if (!dead) setFailed(true)
        return
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5))
      renderer.setClearColor(0xf7f9fc, 1)
      renderer.domElement.style.width = '100%'
      renderer.domElement.style.height = '100%'
      renderer.domElement.style.display = 'block'
      el.appendChild(renderer.domElement)

      const scene = new THREE.Scene()
      scene.fog = new THREE.Fog(0xf7f9fc, 7, 16)
      const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 40)

      const pageTex = new THREE.CanvasTexture(drawPage(false))
      const gapTex = new THREE.CanvasTexture(drawPage(true))
      pageTex.colorSpace = THREE.SRGBColorSpace
      gapTex.colorSpace = THREE.SRGBColorSpace
      const pageMat = new THREE.MeshBasicMaterial({ map: pageTex })
      const gapMat = new THREE.MeshBasicMaterial({ map: gapTex })
      const geometry = new THREE.PlaneGeometry(0.72, 0.96)
      const pages = Array.from({ length: COUNT }, (_, index) => {
        const gap = index === 6
        const mesh = new THREE.Mesh(geometry, gap ? gapMat : pageMat)
        scene.add(mesh)
        return { mesh, gap, index }
      })

      const resize = () => {
        const w = el.clientWidth || window.innerWidth
        const h = el.clientHeight || window.innerHeight
        renderer.setSize(w, h, false)
        camera.aspect = w / Math.max(h, 1)
        camera.updateProjectionMatrix()
      }
      resize()
      const observer = new ResizeObserver(resize)
      observer.observe(el)
      let running = document.visibilityState === 'visible'
      const onVis = () => { running = document.visibilityState === 'visible' }
      document.addEventListener('visibilitychange', onVis)

      let frame = 0
      const tick = () => {
        frame = requestAnimationFrame(tick)
        if (!running) return
        const seconds = performance.now() / 1000
        const scroll = progressRef?.current ?? 0
        pages.forEach(({ mesh, gap, index }) => {
          if (gap) {
            mesh.position.set(1.15, 0.05, 0.8)
            mesh.rotation.set(-0.08, Math.sin(seconds * 0.45) * 0.18, 0.04)
            return
          }
          const travel = (seconds * 0.055 + scroll * 0.25 + index / COUNT) % 1
          const spin = travel * Math.PI * 2 + index
          if (travel > 0.68) {
            const u = (travel - 0.68) / 0.32
            const ease = u * u * (3 - 2 * u)
            const orbit = seconds * 0.35 + index
            mesh.position.set(
              Math.cos(spin) * 2.4 + (Math.cos(orbit) * 1.55 - Math.cos(spin) * 2.4) * ease + 0.35,
              Math.sin(spin) * 0.55 * (1 - ease) + Math.sin(orbit) * 0.22 * ease,
              -1.2 + Math.sin(orbit) * 0.55,
            )
            mesh.rotation.set(0, orbit * 0.35, 0)
          } else {
            const depth = -7 + (travel / 0.68) * 6.2
            mesh.position.set(Math.cos(spin) * (2.6 - travel * 1.4) + 0.4, Math.sin(spin * 2) * 0.7, depth)
            mesh.rotation.set(0, spin * 0.15, Math.sin(spin) * 0.08)
          }
        })
        camera.position.set(Math.sin(seconds * 0.12) * 0.25, 0.55, 5.4 - scroll * 0.6)
        camera.lookAt(0.45, 0.05, 0)
        renderer.render(scene, camera)
      }
      tick()

      cleanup = () => {
        cancelAnimationFrame(frame)
        observer.disconnect()
        document.removeEventListener('visibilitychange', onVis)
        geometry.dispose()
        pageMat.dispose()
        gapMat.dispose()
        pageTex.dispose()
        gapTex.dispose()
        renderer.dispose()
        if (renderer.domElement.parentNode === el) el.removeChild(renderer.domElement)
      }
    })()

    return () => {
      dead = true
      cleanup()
    }
  }, [progressRef, reduced])

  if (reduced || failed) return null

  return <div ref={host} className="fixed inset-0 z-0 pointer-events-none print:hidden" aria-hidden="true" />
}
