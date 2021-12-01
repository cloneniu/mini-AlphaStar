# param for some configs, for ease use of changing different servers
# also ease of use for experiments

'''whether is running on server, on server meaning use GPU with larger memoary'''
on_server = False
#on_server = True

'''The replay path'''
replay_path = "/mini-AlphaStar/scripts/download_replay/third/replay/"
#replay_path = "/home/liuruoze/data4/mini-AlphaStar/data/filtered_replays_1/"
#replay_path = "/home/liuruoze/mini-AlphaStar/data/filtered_replays_1/"

'''The mini scale used in hyperparameter'''
#Mini_Scale = 4
#Mini_Scale = 8
Mini_Scale = 16

'''The training and tesing map for current setting, affecting the location head mask '''
# map_name = 'Simple64'
map_name = 'AbyssalReef'
