// Root app — routing state, mounts pages, wires Tweaks.

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "variant": "field",
  "theme": "light",
  "density": "cozy",
  "libraryLayout": "feed"
}/*EDITMODE-END*/;

function ScribeApp() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [route, setRoute] = React.useState({ page: "library", params: {} });
  const [cmdkOpen, setCmdkOpen] = React.useState(false);

  function navigate(page, params = {}) {
    if (params.openCmdk) { setCmdkOpen(true); return; }
    if (page) setRoute({ page, params });
  }

  // ⌘K / Ctrl-K to open the command palette
  React.useEffect(() => {
    function onKey(e) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setCmdkOpen(o => !o);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Apply variant/theme/density to <html>
  React.useEffect(() => {
    const html = document.documentElement;
    html.setAttribute("data-variant", t.variant);
    html.setAttribute("data-theme", t.theme);
    html.setAttribute("data-density", t.density);
  }, [t.variant, t.theme, t.density]);

  let page = null;
  switch (route.page) {
    case "library":    page = <LibraryPage navigate={navigate} t={t} setTweak={setTweak}/>; break;
    case "transcript": page = <TranscriptDetail id={route.params.id} navigate={navigate}/>; break;
    case "queue":      page = <QueuePage navigate={navigate}/>; break;
    case "job":        page = <JobDetail id={route.params.id} navigate={navigate}/>; break;
    case "ops":        page = <OpsPage navigate={navigate}/>; break;
    case "settings":   page = <SettingsPage t={t} setTweak={setTweak}/>; break;
    default:           page = <LibraryPage navigate={navigate} t={t} setTweak={setTweak}/>;
  }

  return (
    <div className="app">
      <TopBar onOpenCmdk={() => setCmdkOpen(true)} t={t} setTweak={setTweak}/>
      <Sidebar page={route.page} navigate={navigate}/>
      <main className="main" data-screen-label={route.page}>
        {page}
      </main>
      <CommandPalette open={cmdkOpen} onClose={() => setCmdkOpen(false)} navigate={navigate}/>

      <TweaksPanel>
        <TweakSection label="Visual variant"/>
        <TweakSelect label="Variant" value={t.variant}
                    options={["paper","terminal","console","field"]}
                    onChange={(v) => setTweak("variant", v)}/>
        <TweakRadio label="Theme" value={t.theme}
                    options={["light","dark"]}
                    onChange={(v) => setTweak("theme", v)}/>
        <TweakRadio label="Density" value={t.density}
                    options={["compact","cozy","comfy"]}
                    onChange={(v) => setTweak("density", v)}/>

        <TweakSection label="Library"/>
        <TweakRadio label="Layout" value={t.libraryLayout}
                    options={["table","feed","cards"]}
                    onChange={(v) => setTweak("libraryLayout", v)}/>

        <TweakSection label="Jump to"/>
        <TweakButton label="Library" onClick={() => navigate("library")}/>
        <TweakButton label="Transcript · #142" onClick={() => navigate("transcript", { id: 142 })}/>
        <TweakButton label="Queue · 3 in flight" onClick={() => navigate("queue")}/>
        <TweakButton label="Job in flight · #218" onClick={() => navigate("job", { id: 218 })}/>
        <TweakButton label="Partial transcript · #132" onClick={() => navigate("transcript", { id: 132 })}/>
        <TweakButton label="Ops dashboard" onClick={() => navigate("ops")}/>
        <TweakButton label="Settings" onClick={() => navigate("settings")}/>
        <TweakButton label="Open ⌘K palette" onClick={() => setCmdkOpen(true)}/>
      </TweaksPanel>
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<ScribeApp/>);
