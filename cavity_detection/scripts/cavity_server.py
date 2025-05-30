#!/usr/bin/env python3

import rospy
import numpy as np
from cavity_detection_msgs.msg import Roi, RoiList, HorizontalObservation, VerticalObservation
from cavity_detection_msgs.srv import *
import tf.transformations
from visualization_msgs.msg import MarkerArray
import tf
import tf2_ros
from tf.transformations import euler_from_quaternion
from scipy.spatial.transform import Rotation
from cavity_detection.cavity_structs import HorizontalCluster, HorizontalCavity, VerticalCluster, VerticalCavity
from cavity_detection.rviz import publish_temporal, publish_all
from scipy.spatial import KDTree
from std_msgs.msg import Header

verbose = False
WALL_DEPTH = 0.2
TF_RATE = 1

class CavityServer:
    def __init__(self):
        self.horiz_clusters = {}
        self.vert_clusters = {}
        rospy.init_node('cavity_server_node', anonymous=True)
        rospy.Subscriber('/horiz_roi', HorizontalObservation, self.horiz_callback, queue_size=2)
        rospy.Subscriber('/vert_roi', VerticalObservation, self.vert_callback, queue_size=2)
        # Subscribe to the tf of the camera in map frame
        self.tf_listener = tf.TransformListener()
        self.tf_pub = tf2_ros.TransformBroadcaster()
        self.marker_pub = rospy.Publisher('/cavity_detection/cavity_markers', MarkerArray, queue_size=1)
        self.roi_pub = rospy.Publisher('/cavity_detection/target_list', RoiList, queue_size=1)
        self.s0 = rospy.Service('add_roi', AddRoi, self.handle_add_roi)
        self.s1 = rospy.Service('get_nearest_roi', GetNearestRoi, self.handle_get_nearest_roi)
        self.s2 = rospy.Service('update_roi', UpdateRoi, self.handle_update_roi)
        self.s3 = rospy.Service('add_cavity', AddCavity, self.handle_add_cavity)
        self.s4 = rospy.Service('update_cavity', UpdateCavity, self.handle_update_cavity)
        self.s5 = rospy.Service('split_roi', SplitRoi, self.handle_split_roi)
        self.s6 = rospy.Service('get_roi_by_id', GetRoiById, self.handle_get_roi_by_id)
        self.s7 = rospy.Service('move_roi', MoveRoi, self.handle_move_roi)
        self.s8 = rospy.Service('mark_filled', MarkFilled, self.handle_mark_filled)
        self.s9 = rospy.Service('mark_target', MarkTarget, self.handle_mark_target)
        self.next_horizontal = 0
        self.next_vertical = 0
        self.current_target = None
        
        self.kd_tree = None
        self.open_cavities = []

    def horiz_callback(self, msg):
        updated = None
        observation_angle = msg.orientation
        # observation_distance = np.linalg.norm(np.array([msg.pose.position.x, msg.pose.position.y, msg.pose.position.z]))
        try:
            pos, quat = self.tf_listener.lookupTransform("map", msg.header.frame_id, msg.header.stamp)
            transformed_msg = self.transform_observation(msg, pos, quat)
            for roi in self.horiz_clusters.values():
                if roi.is_overlapping(transformed_msg):
                    roi.add_observation(transformed_msg)
                    if updated is not None:
                        self.merge_clusters(updated, roi.id)
                        if verbose: print(f"Merged cavities {updated} and {roi.id}")
                    updated = roi.id
                    if verbose: print(f"Updated cavity {roi.id}")

            if updated is None:
                roi_id = self.next_horiz_id()
                new_roi = HorizontalCluster(roi_id, transformed_msg)
                self.horiz_clusters[roi_id] = new_roi
                if verbose: print(f"Added new cavity {roi_id}")
            
            self.make_tree()

        except (tf.LookupException, tf.ConnectivityException, tf.ExtrapolationException) as e:
            rospy.logwarn(f"Transform error: {e}")

    def vert_callback(self, msg):
        try:
            updated = None
            for roi in self.vert_clusters.values():
                if roi.is_overlapping(msg):
                    roi.add_observation(msg)
                    updated = roi.id
                    if verbose: print(f"Updated cluster {roi.id}")
                    break
            if updated is None:
                roi_id = self.next_vert_id()
                new_roi = VerticalCluster(roi_id, msg)
                self.vert_clusters[roi_id] = new_roi
                if verbose: print(f"Added new cluster {roi_id}")
            
            #self.make_tree()

        except Exception as e:
            rospy.logerr(f"Error in vert_callback: {e}")

    def make_sub_cluster(self, cluster, start, end, new_cluster_id):
        rospy.loginfo("entered make_sub_cluster")
        cluster_lines = cluster.estimated_lines
        print("got lines")
        new_lines = cluster_lines[start:end] # +1 because we need to include the end beam
        flat_lines = new_lines.flatten()
        flat_lines = flat_lines.tolist()
        print(flat_lines)
        new_observation = HorizontalObservation()
        print("made observation")
        new_observation.header.frame_id = "map"
        new_observation.header.stamp = rospy.Time.now()
        new_observation.lines = flat_lines
        new_observation.orientation = cluster.orientation
        new_observation.length = cluster.length
        new_observation.spacing = cluster.spacing
        new_observation.height = cluster.height
        print("done making observation")

        try:
            new_cluster = HorizontalCluster(new_cluster_id, new_observation)
        except Exception as e:
            import traceback
            print("EXCEPTION during HorizontalCluster construction!")
            traceback.print_exc()
            return  # or raise, depending on context
        print("made cluster from observation")
        return new_cluster

    def split_cluster(self, cluster_id, start, end, min_cluster_size=3):
        rospy.loginfo(f"splitting cluster {cluster_id} from {start} to {end}")
        cluster = self.horiz_clusters.get(cluster_id)
        rospy.loginfo("got the cluster")
        end_board = cluster.num_boards - 1
        num_in_segment = end - start

        if num_in_segment < min_cluster_size:
            rospy.loginfo(f"{num_in_segment} < 3, cluster too small")
            return

        if start >= min_cluster_size:
            # make a before cluster
            before_cluster_id = self.next_horiz_id()
            before_cluster = self.make_sub_cluster(cluster, 0, start, before_cluster_id)
            rospy.loginfo(f"{before_cluster.id}: {before_cluster.num_boards} boards at {before_cluster.anchor_point}")

            self.horiz_clusters[before_cluster_id] = before_cluster
            rospy.loginfo(f'created new cluster before: {before_cluster_id}')
        else:
            rospy.loginfo('before too small')

        if end_board - end >= min_cluster_size:
            # make an after cluster
            after_cluster_id = self.next_horiz_id()
            after_cluster = self.make_sub_cluster(cluster, end, end_board, after_cluster_id)
            self.horiz_clusters[after_cluster_id] = after_cluster
            rospy.loginfo(f'created new cluster after: {after_cluster_id}')
        else:
            rospy.loginfo('after too small')

        trimmed_cluster = self.make_sub_cluster(cluster, start, end, cluster_id)
        rospy.loginfo(f'created trimmed cluster')

        self.horiz_clusters[cluster_id] = trimmed_cluster
        rospy.loginfo(f'replaced cluster with trimmed one')

    def next_vert_id(self): 
        id = f'vert_roi_{self.next_vertical}'
        self.next_vertical += 1
        return id
    
    def next_horiz_id(self):
        id = f'horiz_roi_{self.next_horizontal}'
        self.next_horizontal += 1
        return id

    def merge_clusters(self, cluster_1_id, cluster_2_id):
        if verbose: print(f"Merging clusters {cluster_1_id} and {cluster_2_id}")
        # find the bigger cluster
        cluster_1 = self.horiz_clusters[cluster_1_id]
        cluster_2 = self.horiz_clusters[cluster_2_id]
        if cluster_1.length > cluster_2.length:
            bigger_cluster = cluster_1
            smaller_cluster = cluster_2
        else:
            bigger_cluster = cluster_2
            smaller_cluster = cluster_1
        # make smaller one into an observation message
        small_observation = HorizontalObservation()
        small_observation.header = Header()
        small_observation.header.frame_id = "map"
        small_observation.header.stamp = rospy.Time.now()
        small_observation.lines = list(smaller_cluster.estimated_lines.flatten())
        small_observation.orientation = smaller_cluster.orientation
        small_observation.length = smaller_cluster.length
        small_observation.spacing = smaller_cluster.spacing
        small_observation.height = smaller_cluster.height
        # do fusion
        bigger_cluster.add_observation(small_observation)
        # remove smaller cluster from set
        self.horiz_clusters.__delitem__(smaller_cluster.id)
    
    def make_tree(self):
        self.open_cavities = []
        for roi in self.horiz_clusters.values():
            self.open_cavities.append(roi)
        for roi in self.vert_clusters.values():
            self.open_cavities.append(roi)
        self.kd_tree = KDTree([roi.anchor_point for roi in self.open_cavities])

    def transform_observation(self, observation, pos, quat):
        angle = euler_from_quaternion(quat)[2]
        T = np.array([[np.cos(angle),-np.sin(angle), pos[0]], 
                        [np.sin(angle),np.cos(angle), pos[1]], 
                        [0, 0, 1]])
        lines = np.array(observation.lines).reshape(-1, 2)
        lines = np.vstack((lines.T, np.ones((1, len(lines)))))
        transformed_lines = np.dot(T, lines)
        transformed_lines = transformed_lines[:2, :].T
        transformed_lines = transformed_lines.flatten()
        observation.lines = transformed_lines.tolist()
        observation.orientation += angle
        observation.header.frame_id = "map"
        return observation

    def handle_get_nearest_roi(self, req):
        # Logic to handle the request and generate a response
        rospy.loginfo("Received request for nearest ROI")
        robot_pos, _ = self.tf_listener.lookupTransform("map", "base_link", rospy.Time(0))
        if self.kd_tree is None:
            self.make_tree()
            if verbose: print("made tree")
        robot_xy = [robot_pos[0], robot_pos[1]]
        closest_index = self.kd_tree.query(robot_xy)[1]
        closest_roi = self.open_cavities[closest_index]
        response = GetNearestRoiResponse()
        response.roi = Roi()
        response.roi.id = closest_roi.id
        response.roi.length = closest_roi.length
        response.roi.width = closest_roi.width
        response.roi.depth = closest_roi.height
        response.roi.num_cavities = closest_roi.num_cavities
        response.roi.cavity_width = closest_roi.spacing
        return response
    
    def handle_get_roi_by_id(self, req):
        # Logic to handle the request and generate a response
        rospy.loginfo("Received request for named ROI")
        response = GetRoiByIdResponse()
        response.roi = Roi()
        roi = self.horiz_clusters.get(req.id)
        if roi is not None:
            response.roi.id = roi.id
            response.roi.length = roi.length
            response.roi.width = roi.width
            response.roi.depth = roi.height
            response.roi.num_cavities = roi.num_cavities
            response.roi.cavity_width = roi.spacing
        return response

    def handle_add_roi(self, req):
        # Logic to update status and generate response
        roi_id = self.next_horiz_id()
        new_roi = HorizontalCluster(roi_id, req.x, req.y, req.theta, req.length, req.height, req.spacing, req.num_cavities)
        self.horiz_clusters[roi_id] = new_roi
        response = AddRoiResponse()
        response.roi_id = roi_id
        return response

    def handle_move_roi(self, req):
        # Logic to update status and generate response
        roi = self.horiz_clusters.get(req.roi_id)
        roi.manual_move_transform(dtheta=req.dtheta, dx=req.dx, dy=req.dy)
        response = MoveRoiResponse()
        response.success = True
        return response
    
    def handle_update_roi(self, req):
        # Logic to update status and generate response
        roi = self.horiz_clusters.get(req.roi_id)

        if req.length != 0.0:
            roi.manual_update_attributes(length=req.length)
        if req.height != 0.0:
            roi.manual_update_attributes(height=req.height)
        if req.spacing != 0.0:
            roi.manual_update_attributes(spacing=req.spacing)
        if req.num_cavities != 0:
            roi.manual_update_attributes(num_cavities=req.num_cavities)

        response = UpdateRoiResponse()
        response.success = True
        return response
    
    def handle_split_roi(self, req):
        # Logic to update status and generate response
        roi = self.horiz_clusters.get(req.roi_id)
        print(f'changing {roi.id} which has {roi.num_boards} boards right now.')
        response = SplitRoiResponse()
        try:
            self.split_cluster(roi.id, req.start, req.end)
            response.success = True
        except Exception as e:
            response.success = False
        return response

    def handle_add_cavity(self, req):
        # Logic to update status and generate response
        roi_id = req.roi_id
        roi = self.horiz_cavities[roi_id]
        next_cavity = len(roi.cavities)
        cavity_id = f'{roi_id}_{next_cavity}'
        front = np.array([0, req.y_offset, 0])
        width = req.width
        cavity = HorizontalCavity(cavity_id, roi_id, front, roi.orientation, roi.length, width, roi.depth)

        roi.cavities[cavity_id] = cavity
        response = AddCavityResponse()
        response.cavity_id = cavity_id
        response.success = True
        return response

    def handle_update_cavity(self, req):
        # Logic to update status and generate response
        cavity_id = req.cavity_id
        roi = self.horiz_clusters.get(req.roi_id)
        cavity = roi.cavities[cavity_id]
        if req.y_offset != 0.0:
            roi.y_offset = req.y_offset
        if req.width != 0.0:
            cavity.width = req.width
        if req.status:
            cavity.status = req.status
        response = UpdateCavityResponse()
        response.success = True
        return response
    
    def handle_mark_filled(self, req):
        # Logic to update filled status
        roi = self.horiz_clusters.get(req.roi_id)
        roi.is_filled = True
        response = MarkFilledResponse()
        response.success = True
        return response
    
    def handle_mark_target(self, req):
        # Logic to update filled status
        response = MarkTargetResponse()
        if self.current_target is not None:
            current_target = self.horiz_clusters.get(self.current_target)
            current_target.is_current_target = False
        new_target = self.horiz_clusters.get(req.roi_id)
        new_target.is_current_target = True
        self.current_target = req.roi_id
        response.success = True
        return response
    
    def run_publish_all(self, event=None): 
        publish_all(self.tf_pub, self.marker_pub, self.roi_pub, self.horiz_clusters, self.vert_clusters)
    
    def run(self):
        # Set up a timer to publish markers at a fixed rate
        rospy.Timer(rospy.Duration(1.0/TF_RATE), self.run_publish_all)
        if verbose: print("Timer set")
        rospy.spin()

def run():        
    cavity_server = CavityServer()
    cavity_server.run()

if __name__ == '__main__':
    try:
        run()
    except rospy.ROSInterruptException:
        pass
