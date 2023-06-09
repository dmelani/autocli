import json
from PIL import Image
import requests
import base64
from io import BytesIO
from argparse import ArgumentParser 
import asyncio
import aiohttp
import sys
import time

models_LUT = {
        "aom3a2": ("more_models_anime_OrangeMixs_Models_AbyssOrangeMix3_AOM3A2_orangemixs", "orangemix.vae.pt"),
        "aom3a1b": ("more_models_anime_OrangeMixs_Models_AbyssOrangeMix3_AOM3A1B_orangemixs", "orangemix.vae.pt"),
        "darksushi": ("more_models_anime_DarkSushiMix_darkSushiMixMix_brighterPruned", "vae-ft-mse-840000-ema-pruned.safetensors"),
        "deliberate": ("more_models_allround_Deliberate_deliberate_v2", "vae-ft-mse-840000-ema-pruned.safetensors"),
        "chilloutmix": ("more_models_allround_ChilloutMix_chilloutmix_NiPrunedFp32Fix", "vae-ft-mse-840000-ema-pruned.safetensors"),
        "rpg": ("more_models_allround_RPG_rpg_V4", "vae-ft-mse-840000-ema-pruned.safetensors"),
        "rev": ("more_models_allround_Realistic Vision_realisticVisionV20_v20", "vae-ft-mse-840000-ema-pruned.safetensors"),
        "rev_animated": ("more_models_allround_ReV Animated_revAnimated_v11", "kl-f8-anime2.ckpt"),
        "anythingv5": ("more_models_anime_Anything V5_AnythingV3V5_v5PrtRE", "kl-f8-anime2.ckpt"),
        "illuminati": ("more_models_allround_Illuminati Diffusion v1.1_illuminatiDiffusionV1_v11", "vae-ft-mse-840000-ema-pruned.safetensors")
}

sampler_LUT = {
        "euler_a": ("Euler a", 20),
        "ddim": ("DDIM", 50),
        "dpmpp_sde_ka": ("DPM++ SDE Karras", 31),
        "dpmpp_2m_ka": ("DPM++ 2M Karras", 31),
        "dpmpp_2s_a_ka": ("DPM++ 2S a Karras", 31),
        "heun": ("Heun", 50)
        }

dimensions_LUT = {
        "square": (512, 512),
        "lsquare": (768, 768),
        "landscape": (768, 512),
        "portrait": (512, 768)
        }

upscalers_LUT = {
        "normal": "R-ESRGAN 4x+",
        "anime" : "R-ESRGAN 4x+ Anime6B"
        }

pics_args_parse = ArgumentParser()
pics_args_parse.add_argument("-H", dest="host", help="Automatic1111 host", default="127.0.0.1:7860", type=str)
pics_args_parse.add_argument("--nsfw", help="Allow nsfw content", default=False, action='store_true')
pics_args_parse.add_argument("-n", help="Number of pictures", default=1, type=int)
pics_args_parse.add_argument("--cfgs", help="Classifier Free Guidance Scale - how strongly the image should conform to prompt - lower values produce more creative results. Default is 7.", default=7, type=int)
pics_args_parse.add_argument("-m", "--model", dest="data_model", help=f"Stable diffusion model", choices=models_LUT.keys(), default="deliberate", type=str)
pics_args_parse.add_argument("-s", "--sampler", dest="sampler_name", help=f"Stable diffusion sampler", choices=sampler_LUT.keys(), default="dpmpp_sde_ka", type=str)
pics_args_parse.add_argument("-i", dest="sampler_steps", help="Number of sampler steps", default=None, type=int)
pics_args_parse.add_argument("-l", "--layout", dest="layout", default="square", choices=["square", "lsquare", "portrait", "landscape"])
pics_args_parse.add_argument("--clip_stop", dest="clip_stop", help="Sets where to stop the CLIP language model. Default is 1. It works kinda like this in layers person -> male, female -> man, boy, woman girl -> and so on", default=1, choices=range(1, 5), type=int)
pics_args_parse.add_argument("prompt", type=str)
pics_args_parse.add_argument("neg_prompt", metavar="negative prompt", type=str, nargs='?', default="(bad quality, worst quality:1.4), child, kid, toddler")
pics_args_parse.add_argument("--restore_faces", help="Attempts to restore faces", default=False, action='store_true')
pics_args_parse.add_argument("-U", "--upscale", dest="upscaler", help=f"Upscale by 2x. Use with caution because it takes a lot of time to do.", default=None, choices=upscalers_LUT.keys())

class Txt2Img:
    def __init__(self, prompt = "Dingle dot the test bot", negative_prompt = "", sampler_name="DPM++ SDE Karras", steps=30, filter_nsfw = True, batch_size=1, model=None, vae=None, width=512, height=512, clip_stop=1, restore_faces=False, cfg_scale=7, upscaler=None):
        self.prompt = prompt
        self.negative_prompt = negative_prompt
        self.sampler_name = sampler_name
        self.steps = steps
        self.n_iter = batch_size
        self.width = width
        self.height = height
        self.restore_faces = restore_faces
        self.cfg_scale = cfg_scale

        self.override_settings = {
            "filter_nsfw" : filter_nsfw,
            "CLIP_stop_at_last_layers": clip_stop
        }
        if model:
            self.override_settings["sd_model_checkpoint"] = model
        if vae:
            self.override_settings["sd_vae"] = vae

        if upscaler:
            self.hr_upscaler = upscaler
            self.enable_hr = True
            self.hr_scale = 2
            self.denoising_strength = 0.2

        self.override_settings_restore_afterwards = True

    def to_json(self):
        return json.dumps(self, default=lambda o: o.__dict__)
        
class Txt2ImgResponse:
    def __init__(self, images, parameters, info):
        self.images = images 
        self.parameters = parameters
        self.info = info

def parse_txt2img_respones(data):
    d = json.loads(data)
    return Txt2ImgResponse(d['images'], d['parameters'], d['info'])

async def render():
    args = pics_args_parse.parse_args()
        
    host = args.host
    prompt = args.prompt
    neg_prompt = args.neg_prompt
    batch_size = args.n
    filter_nsfw = False if args.nsfw is True else True
    data_model = args.data_model
    width, height = dimensions_LUT[args.layout]
    clip_stop = args.clip_stop
    restore_faces = args.restore_faces
    cfgs = args.cfgs
    sampler = args.sampler_name
    upscaler = args.upscaler
    sampler_steps = args.sampler_steps

    if filter_nsfw and "nsfw" not in neg_prompt:
        neg_prompt = "(nsfw:1.1), " + neg_prompt

    if data_model is not None and data_model not in models_LUT:
        print(f"No such model: {data_model}")
        return

    model = None
    vae = None
    if data_model:
        model, vae = models_LUT[data_model]
        
    sampler_name = None
    steps = None
    if sampler:
        sampler_name, steps = sampler_LUT[sampler]

    if sampler_steps:
        steps = sampler_steps

    upscaler_name = None
    if upscaler:
        upscaler_name = upscalers_LUT[upscaler]
    
    print(f"Rendering {prompt}")

    t = Txt2Img(prompt=prompt, negative_prompt=neg_prompt, filter_nsfw=filter_nsfw, batch_size=batch_size, model=model, vae=vae, width=width, height=height, clip_stop=clip_stop, restore_faces=restore_faces, cfg_scale=cfgs, sampler_name=sampler_name, steps=steps, upscaler=upscaler_name)
    async with aiohttp.ClientSession() as session:
        async with session.post(f'http://{host}/sdapi/v1/txt2img', data=t.to_json(), headers={'Content-type': 'application/json'}) as response:
            r_data = await response.text()

    resp = parse_txt2img_respones(r_data)

    num_saved_files = 0
    try:
        ts = int(time.time())
        for i, x in enumerate(resp.images):
            pic = base64.b64decode(x)

            img = Image.open(BytesIO(pic))
            if not img.getbbox():
                # All black image
                continue

            #Save file here
            num_saved_files += 1
            name = f"{ts}_{i}.png"
            with open(name, "wb") as file:
                print(f"Saving {name}")
                file.write(pic)

    except Exception as e:
        print(f"Failed to generate pic: {e}")
        return

    diff_len = batch_size - num_saved_files
    if diff_len > 0:
        print(f"Some pics were too spicy for me")

async def main():
    await render()

if __name__=="__main__":
    asyncio.run(main())
