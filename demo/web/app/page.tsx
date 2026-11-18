import Demo from "@/components/Demo";
import { startFrom, tilesFrom } from "@/lib/start";

type Params = Promise<Record<string, string | string[] | undefined>>;

// Read when the page is asked for, not when it is built: one image, any municipality.
export default async function Page({ searchParams }: { searchParams: Params }) {
  const asked = await searchParams;
  const one = (name: string) => {
    const value = asked[name];
    return Array.isArray(value) ? value[0] : value;
  };
  const env = process.env;
  const start = startFrom(
    { lat: one("lat"), lon: one("lon"), zoom: one("zoom"), place: one("place"), layer: one("layer"), frame: one("frame") },
    {
      lat: env.DPC_RADAR_LAT,
      lon: env.DPC_RADAR_LON,
      zoom: env.DPC_RADAR_ZOOM,
      place: env.DPC_RADAR_PLACE,
      layer: env.DPC_RADAR_LAYER,
    },
  );
  return <Demo start={start} tiles={tilesFrom(env.DPC_RADAR_TILES, env.DPC_RADAR_TILES_CREDIT)} />;
}
